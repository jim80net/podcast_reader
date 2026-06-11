"""Persistent job journal with single-worker FIFO execution and SSE fan-out.

The :class:`JobStore` owns the job state machine
(``queued`` → ``running`` → ``done`` | ``failed`` | ``interrupted``, plus
``awaiting-confirmation`` reserved for later phases). Every state transition
and every pipeline event is journaled atomically to ``<data_dir>/jobs.json``,
which is what makes startup interrupted-marking and restart recovery work.

Exactly one daemon worker thread executes jobs, so at most one job runs at a
time and submissions are processed FIFO. SSE clients subscribe via bounded
per-client queues; when a queue is full the oldest event is dropped, and a
queue that stays full for ``SUBSCRIBER_FULL_STREAK_LIMIT`` consecutive
publishes is pruned as abandoned (cleanup never depends on GC).
"""

from __future__ import annotations

import contextlib
import copy
import json
import logging
import queue
import threading
import time
import uuid
from typing import TYPE_CHECKING, cast

from podcast_reader.engine.settings import atomic_write_json
from podcast_reader.pipeline import PipelineError
from podcast_reader.types import JobError, PipelineEvent, new_job_record

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from podcast_reader.types import JobRecord, JobState, PipelineResult

    JobRunner = Callable[[JobRecord, Callable[[PipelineEvent], None]], PipelineResult]

logger = logging.getLogger(__name__)

JOURNAL_FILE = "jobs.json"
SUBSCRIBER_QUEUE_SIZE = 256
SUBSCRIBER_FULL_STREAK_LIMIT = 100
MAX_TERMINAL_JOBS = 200

_TERMINAL_STATES = frozenset({"done", "failed", "interrupted"})


class JobStore:
    """Journal-backed FIFO job store with a single worker thread."""

    def __init__(self, data_dir: Path, runner: JobRunner) -> None:
        self._data_dir = data_dir
        self._runner = runner
        self._lock = threading.RLock()
        self._jobs: dict[str, JobRecord] = {}
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        # subscriber queue → count of consecutive publishes it has been full for
        self._subscribers: dict[queue.Queue[PipelineEvent], int] = {}
        self._recover_journal()

    # -- public API ------------------------------------------------------

    def submit(self, source: str, title: str | None) -> JobRecord:
        """Create a queued job and enqueue it for the worker. Always ``queued``."""
        record = new_job_record(job_id=uuid.uuid4().hex, source=source, title=title)
        now = time.time()
        record["created_at"] = now
        record["updated_at"] = now
        with self._lock:
            self._jobs[record["id"]] = record
            self._write_journal()
        self._queue.put(record["id"])
        return copy.deepcopy(record)

    def get(self, job_id: str) -> JobRecord:
        """Snapshot of one job record (raises ``KeyError`` when unknown)."""
        with self._lock:
            return copy.deepcopy(self._jobs[job_id])

    def list_jobs(self) -> list[JobRecord]:
        """Snapshots of all job records in submission order."""
        with self._lock:
            return [copy.deepcopy(record) for record in self._jobs.values()]

    def start_worker(self) -> None:
        """Start the single worker thread (idempotent; restartable after shutdown).

        Clears the stop event so a store restarted after :meth:`shutdown`
        processes jobs again instead of exiting on its first dequeue.
        """
        with self._lock:
            if self._worker is not None:
                return
            self._stop.clear()
            self._worker = threading.Thread(target=self._work_loop, name="job-worker", daemon=True)
            self._worker.start()

    def shutdown(self) -> None:
        """Stop the worker after the current job finishes, skipping the backlog.

        The stop event makes the worker exit on its next dequeue instead of
        draining queued jobs first; those jobs stay ``queued`` in the journal
        and startup recovery re-enqueues them on the next run. The sentinel
        only wakes a worker blocked on an empty queue.
        """
        with self._lock:
            worker = self._worker
            self._worker = None
        if worker is None:
            return
        self._stop.set()
        self._queue.put(None)
        worker.join(timeout=30)

    def subscribe(self) -> queue.Queue[PipelineEvent]:
        """Register a bounded event queue for SSE fan-out."""
        q: queue.Queue[PipelineEvent] = queue.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
        with self._lock:
            self._subscribers[q] = 0
        return q

    def unsubscribe(self, q: queue.Queue[PipelineEvent]) -> None:
        """Remove a subscriber queue (safe to call twice)."""
        with self._lock:
            self._subscribers.pop(q, None)

    @property
    def subscriber_count(self) -> int:
        """Number of live SSE subscribers (used by disconnect-cleanup tests)."""
        with self._lock:
            return len(self._subscribers)

    # -- worker ----------------------------------------------------------

    def _work_loop(self) -> None:
        while True:
            job_id = self._queue.get()
            if self._stop.is_set():
                # On stop, a dequeued job is left untouched: it is still
                # ``queued`` in the journal, so recovery re-enqueues it.
                return
            if job_id is None:
                # Stale wake-up sentinel from a previous shutdown (the exiting
                # worker dequeued a job id instead); not an exit signal now.
                continue
            try:
                self._run_job(job_id)
            except Exception as exc:  # the only worker must survive anything
                logger.exception("Job %s escaped _run_job; marking failed", job_id)
                # Best effort: the journal write inside _transition may be the
                # very thing that failed, so guard it too.
                with contextlib.suppress(Exception):
                    self._transition(
                        job_id,
                        "failed",
                        error=JobError(code="internal", message=str(exc), hint=""),
                    )

    def _run_job(self, job_id: str) -> None:
        self._transition(job_id, "running")
        with self._lock:
            record = copy.deepcopy(self._jobs[job_id])

        def on_event(event: PipelineEvent) -> None:
            tagged = PipelineEvent(
                kind=event["kind"],
                step=event["step"],
                message=event["message"],
                data={**event["data"], "job_id": job_id},
            )
            with self._lock:
                live = self._jobs[job_id]
                live["events"].append(tagged)
                live["updated_at"] = time.time()
                self._write_journal()
            self._publish(tagged)

        try:
            result = self._runner(record, on_event)
        except PipelineError as exc:
            self._fail(
                job_id, JobError(code=exc.code, message=exc.message, hint=exc.hint), on_event
            )
        except Exception as exc:  # worker must never die; unexpected → internal
            self._fail(job_id, JobError(code="internal", message=str(exc), hint=""), on_event)
        else:
            self._transition(job_id, "done", result=result)

    def _fail(
        self,
        job_id: str,
        error: JobError,
        on_event: Callable[[PipelineEvent], None],
    ) -> None:
        self._transition(job_id, "failed", error=error)
        on_event(
            PipelineEvent(
                kind="job_failed",
                step=None,
                message=error["message"],
                data={"code": error["code"], "hint": error["hint"]},
            )
        )

    # -- journal ---------------------------------------------------------

    def _transition(
        self,
        job_id: str,
        state: JobState,
        *,
        result: PipelineResult | None = None,
        error: JobError | None = None,
    ) -> None:
        with self._lock:
            record = self._jobs[job_id]
            record["state"] = state
            record["updated_at"] = time.time()
            if result is not None:
                record["result"] = result
            if error is not None:
                record["error"] = error
            self._write_journal()

    def _recover_journal(self) -> None:
        """Load the journal, flipping ``running`` → ``interrupted`` and
        re-enqueueing persisted ``queued`` jobs in ``created_at`` order so the
        FIFO promise survives a restart.

        An unreadable or wrong-shaped journal is quarantined as
        ``jobs.json.corrupt`` (with a warning) and recovery continues empty —
        a corrupt journal must never prevent the engine from serving.
        """
        path = self._data_dir / JOURNAL_FILE
        if not path.exists():
            return
        try:
            records = cast("list[JobRecord]", json.loads(path.read_text()))
            jobs: dict[str, JobRecord] = {}
            queued: list[JobRecord] = []
            interrupted = False
            for record in records:
                if record["state"] == "running":
                    record["state"] = "interrupted"
                    record["updated_at"] = time.time()
                    interrupted = True
                elif record["state"] == "queued":
                    queued.append(record)
                jobs[record["id"]] = record
            queued.sort(key=lambda r: r["created_at"])
        except (OSError, ValueError, TypeError, KeyError) as exc:
            corrupt = path.with_name(path.name + ".corrupt")
            try:
                path.replace(corrupt)
            except OSError as rename_exc:
                logger.warning(
                    "Job journal unreadable (%s); quarantine rename failed (%s); starting empty",
                    exc,
                    rename_exc,
                )
                return
            logger.warning(
                "Job journal unreadable; quarantined to %s and starting empty: %s", corrupt, exc
            )
            return
        with self._lock:
            self._jobs.update(jobs)
            if interrupted:
                self._write_journal()
        for record in queued:
            self._queue.put(record["id"])

    def _write_journal(self) -> None:
        """Atomic journal write with terminal-job retention; caller holds the lock."""
        self._prune_terminal_jobs()
        atomic_write_json(self._data_dir / JOURNAL_FILE, list(self._jobs.values()))

    def _prune_terminal_jobs(self) -> None:
        """Drop the oldest terminal jobs beyond ``MAX_TERMINAL_JOBS``.

        Non-terminal jobs (queued/running/awaiting-confirmation) are always
        retained; the cap keeps the rewrite-the-whole-journal cost bounded.
        """
        terminal = [r for r in self._jobs.values() if r["state"] in _TERMINAL_STATES]
        excess = len(terminal) - MAX_TERMINAL_JOBS
        if excess <= 0:
            return
        terminal.sort(key=lambda r: r["updated_at"])
        for record in terminal[:excess]:
            del self._jobs[record["id"]]

    # -- fan-out ---------------------------------------------------------

    def _publish(self, event: PipelineEvent) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        full = {q: self._offer(q, event) for q in subscribers}
        with self._lock:
            for q, was_full in full.items():
                if q not in self._subscribers:
                    continue  # unsubscribed while we were publishing
                self._subscribers[q] = self._subscribers[q] + 1 if was_full else 0
                if self._subscribers[q] >= SUBSCRIBER_FULL_STREAK_LIMIT:
                    del self._subscribers[q]  # abandoned consumer

    @staticmethod
    def _offer(q: queue.Queue[PipelineEvent], event: PipelineEvent) -> bool:
        """Enqueue *event*, dropping the oldest when full; True when it was full."""
        was_full = False
        while True:
            try:
                q.put_nowait(event)
                return was_full
            except queue.Full:
                was_full = True
                try:
                    q.get_nowait()  # drop the oldest event
                except queue.Empty:  # pragma: no cover — racing consumer
                    continue
