"""Persistent job journal with single-worker FIFO execution and SSE fan-out.

The :class:`JobStore` owns the job state machine
(``queued`` → ``running`` → ``done`` | ``failed`` | ``interrupted``, plus
``awaiting-confirmation`` reserved for later phases). Every state transition
and every pipeline event is journaled atomically to ``<data_dir>/jobs.json``,
which is what makes startup interrupted-marking and restart recovery work.

Exactly one daemon worker thread executes jobs, so at most one job runs at a
time and submissions are processed FIFO. SSE clients subscribe via bounded
per-client queues; when a queue is full the oldest event is dropped.
"""

from __future__ import annotations

import copy
import json
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

JOURNAL_FILE = "jobs.json"
SUBSCRIBER_QUEUE_SIZE = 256


class JobStore:
    """Journal-backed FIFO job store with a single worker thread."""

    def __init__(self, data_dir: Path, runner: JobRunner) -> None:
        self._data_dir = data_dir
        self._runner = runner
        self._lock = threading.RLock()
        self._jobs: dict[str, JobRecord] = {}
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._subscribers: list[queue.Queue[PipelineEvent]] = []
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
        """Start the single worker thread (idempotent)."""
        with self._lock:
            if self._worker is not None:
                return
            self._worker = threading.Thread(target=self._work_loop, name="job-worker", daemon=True)
            self._worker.start()

    def shutdown(self) -> None:
        """Stop the worker after the current job finishes."""
        with self._lock:
            worker = self._worker
            self._worker = None
        if worker is None:
            return
        self._queue.put(None)
        worker.join(timeout=30)

    def subscribe(self) -> queue.Queue[PipelineEvent]:
        """Register a bounded event queue for SSE fan-out."""
        q: queue.Queue[PipelineEvent] = queue.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue[PipelineEvent]) -> None:
        """Remove a subscriber queue (safe to call twice)."""
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    @property
    def subscriber_count(self) -> int:
        """Number of live SSE subscribers (used by disconnect-cleanup tests)."""
        with self._lock:
            return len(self._subscribers)

    # -- worker ----------------------------------------------------------

    def _work_loop(self) -> None:
        while True:
            job_id = self._queue.get()
            if job_id is None:
                return
            self._run_job(job_id)

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
        FIFO promise survives a restart."""
        path = self._data_dir / JOURNAL_FILE
        if not path.exists():
            return
        records = cast("list[JobRecord]", json.loads(path.read_text()))
        interrupted = False
        for record in records:
            if record["state"] == "running":
                record["state"] = "interrupted"
                record["updated_at"] = time.time()
                interrupted = True
            self._jobs[record["id"]] = record
        if interrupted:
            self._write_journal()
        queued = [r for r in self._jobs.values() if r["state"] == "queued"]
        for record in sorted(queued, key=lambda r: r["created_at"]):
            self._queue.put(record["id"])

    def _write_journal(self) -> None:
        """Atomic journal write; caller must hold the lock."""
        atomic_write_json(self._data_dir / JOURNAL_FILE, list(self._jobs.values()))

    # -- fan-out ---------------------------------------------------------

    def _publish(self, event: PipelineEvent) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for q in subscribers:
            while True:
                try:
                    q.put_nowait(event)
                    break
                except queue.Full:
                    try:
                        q.get_nowait()  # drop the oldest event
                    except queue.Empty:  # pragma: no cover — racing consumer
                        continue
