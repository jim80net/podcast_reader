"""Persistent job journal with single-worker FIFO execution and SSE fan-out.

The :class:`JobStore` owns the job state machine
(``queued`` → ``running`` → ``done`` | ``failed`` | ``interrupted``, plus
``awaiting-confirmation`` for jobs submitted with ``requires_confirmation`` —
they are journaled but never enqueued until :meth:`JobStore.confirm`). Every
state transition and every pipeline event is journaled atomically to
``<data_dir>/jobs.json``, which is what makes startup interrupted-marking and
restart recovery work.

Exactly one daemon worker thread executes jobs, so at most one job runs at a
time and submissions are processed FIFO. Event fan-out lives on the shared
:class:`~podcast_reader.engine.events.EventBus` (the public publish seam,
per S6); the store publishes job events into it and delegates the
subscription surface to it.
"""

from __future__ import annotations

import collections
import contextlib
import copy
import json
import logging
import threading
import time
import uuid
from typing import TYPE_CHECKING, cast

from podcast_reader.engine.events import EventBus
from podcast_reader.engine.settings import atomic_write_json
from podcast_reader.pipeline import PipelineError
from podcast_reader.types import JobError, PipelineEvent, new_job_record

if TYPE_CHECKING:
    import queue
    from collections.abc import Callable
    from pathlib import Path

    from podcast_reader.types import JobRecord, JobState, PipelineResult

    JobRunner = Callable[[JobRecord, Callable[[PipelineEvent], None]], PipelineResult]

logger = logging.getLogger(__name__)

JOURNAL_FILE = "jobs.json"
MAX_TERMINAL_JOBS = 200

_TERMINAL_STATES = frozenset({"done", "failed", "interrupted"})


class JobStateError(Exception):
    """A transition was requested from a state that does not allow it.

    The message names the job's current state and is self-authored, so the
    API layer may surface it verbatim (as a 409 detail).
    """


class _WakeQueue:
    """FIFO whose blocking get also wakes when the caller's stop event is set.

    Replaces ``queue.Queue`` + ``None`` sentinel: a sentinel can be stolen by
    a successor worker (leaving the stopping worker blocked forever), and a
    stopping worker that dequeues a job must hand it back, breaking FIFO
    order. Here a stopping worker exits without dequeuing anything, so the
    backlog reaches its successor untouched and in order.
    """

    def __init__(self) -> None:
        self._items: collections.deque[str] = collections.deque()
        self._cond = threading.Condition()

    def put(self, item: str) -> None:
        with self._cond:
            self._items.append(item)
            self._cond.notify_all()

    def wake_all(self) -> None:
        """Wake blocked getters so they re-check their stop event."""
        with self._cond:
            self._cond.notify_all()

    def get_or_stop(self, stop: threading.Event) -> str | None:
        """Block until an item arrives (returned) or *stop* is set (``None``).

        Stop wins over a non-empty queue: a stopping worker must skip the
        backlog, not drain it.
        """
        with self._cond:
            while not self._items and not stop.is_set():
                self._cond.wait()
            if stop.is_set():
                return None
            return self._items.popleft()


class JobStore:
    """Journal-backed FIFO job store with a single worker thread."""

    def __init__(self, data_dir: Path, runner: JobRunner, *, bus: EventBus | None = None) -> None:
        self._data_dir = data_dir
        self._runner = runner
        self._lock = threading.RLock()
        self._jobs: dict[str, JobRecord] = {}
        self._queue = _WakeQueue()
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        # Per P2: while stopping, a failing job is journaled "interrupted",
        # not "failed" — its failure is a consequence of the shutdown itself
        # (children killed under it), not of the job.
        self._stopping = False
        # The shared publish seam (per S6): serve_engine constructs one bus
        # and hands it to both this store and the pack manager.
        self._bus = bus if bus is not None else EventBus()
        self._recover_journal()

    # -- public API ------------------------------------------------------

    def submit(
        self, source: str, title: str | None, *, requires_confirmation: bool = False
    ) -> JobRecord:
        """Create a job and enqueue it for the worker (default: ``queued``).

        With *requires_confirmation* the job is journaled in
        ``awaiting-confirmation`` and NOT enqueued: it never runs until an
        explicit :meth:`confirm` (protocol-initiated jobs use this so nothing
        attacker-supplied auto-executes).
        """
        record = new_job_record(job_id=uuid.uuid4().hex, source=source, title=title)
        if requires_confirmation:
            record["state"] = "awaiting-confirmation"
        now = time.time()
        record["created_at"] = now
        record["updated_at"] = now
        with self._lock:
            self._jobs[record["id"]] = record
            self._write_journal()
        if not requires_confirmation:
            self._queue.put(record["id"])
        return copy.deepcopy(record)

    def confirm(self, job_id: str) -> JobRecord:
        """Transition an awaiting-confirmation job to ``queued`` and enqueue it.

        Raises ``KeyError`` for an unknown job and :class:`JobStateError` from
        any state other than ``awaiting-confirmation``.
        """
        with self._lock:
            record = self._jobs[job_id]
            self._require_awaiting_confirmation(record, "confirm")
            record["state"] = "queued"
            record["updated_at"] = time.time()
            self._write_journal()
            snapshot = copy.deepcopy(record)
        self._queue.put(job_id)
        return snapshot

    def discard(self, job_id: str) -> None:
        """Remove an awaiting-confirmation job from the journal.

        Raises ``KeyError`` for an unknown job and :class:`JobStateError` from
        any state other than ``awaiting-confirmation`` (terminal-job deletion
        is deliberately not supported here).
        """
        with self._lock:
            record = self._jobs[job_id]
            self._require_awaiting_confirmation(record, "discard")
            del self._jobs[job_id]
            self._write_journal()

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

        Each worker generation gets its own stop event — never cleared — so a
        restart racing a slow :meth:`shutdown` cannot un-signal the old worker
        (which would otherwise drain the backlog and loop forever alongside
        the new one).
        """
        with self._lock:
            if self._worker is not None:
                return
            stop = threading.Event()
            self._stop = stop
            self._stopping = False  # a new generation is not stopping
            self._worker = threading.Thread(
                target=self._work_loop, args=(stop,), name="job-worker", daemon=True
            )
            self._worker.start()

    def begin_shutdown(self) -> None:
        """Mark the store stopping without waiting for the worker.

        Sets the stopping flag (per P2: a job failing from here on is
        journaled ``interrupted``) and signals the worker to exit at its next
        dequeue. ``serve_engine`` calls this *before* killing children, so the
        in-flight job's resulting failure is already attributable to shutdown.
        """
        with self._lock:
            self._stopping = True
            stop = self._stop
        stop.set()
        self._queue.wake_all()

    def shutdown(self) -> None:
        """Stop the worker after the current job finishes, skipping the backlog.

        Sets the stopping flag (via :meth:`begin_shutdown`), then joins the
        worker. The worker's stop event takes priority over queued items, so
        it exits on its next dequeue without touching the backlog; those jobs
        stay ``queued`` in the journal (startup recovery re-enqueues them) and
        in the live queue, in order, for any restarted worker.
        """
        self.begin_shutdown()
        with self._lock:
            worker = self._worker
            self._worker = None
        if worker is None:
            return
        worker.join(timeout=30)

    @property
    def bus(self) -> EventBus:
        """The shared event-publish seam this store publishes into (per S6)."""
        return self._bus

    def subscribe(self) -> queue.Queue[PipelineEvent]:
        """Register a bounded event queue for SSE fan-out (delegates to the bus)."""
        return self._bus.subscribe()

    def unsubscribe(self, q: queue.Queue[PipelineEvent]) -> None:
        """Remove a subscriber queue (delegates to the bus; safe to call twice)."""
        self._bus.unsubscribe(q)

    @property
    def subscriber_count(self) -> int:
        """Number of live SSE subscribers (used by disconnect-cleanup tests)."""
        return self._bus.subscriber_count

    # -- worker ----------------------------------------------------------

    def _work_loop(self, stop: threading.Event) -> None:
        while True:
            job_id = self._queue.get_or_stop(stop)
            if job_id is None:
                # Stop requested; the backlog was never dequeued, so it
                # remains intact and in order for a successor worker.
                return
            try:
                self._run_job(job_id)
            except Exception as exc:  # the only worker must survive anything
                logger.exception("Job %s escaped _run_job; marking failed", job_id)
                # Best effort: the journal write inside _transition may be the
                # very thing that failed, so guard it too.
                with contextlib.suppress(Exception):
                    self._transition(
                        job_id,
                        self._failure_state(),
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
            self._bus.publish(tagged)

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
        state = self._failure_state()
        self._transition(job_id, state, error=error)
        if state == "interrupted":
            # Per P2: a failure during shutdown is the shutdown's doing — the
            # job is recoverable (retry affordance), so no job_failed event.
            return
        on_event(
            PipelineEvent(
                kind="job_failed",
                step=None,
                message=error["message"],
                data={"code": error["code"], "hint": error["hint"]},
            )
        )

    def _failure_state(self) -> JobState:
        """``interrupted`` while the store is stopping, ``failed`` otherwise (per P2)."""
        with self._lock:
            return "interrupted" if self._stopping else "failed"

    @staticmethod
    def _require_awaiting_confirmation(record: JobRecord, action: str) -> None:
        """Raise :class:`JobStateError` unless *record* awaits confirmation."""
        if record["state"] != "awaiting-confirmation":
            raise JobStateError(
                f"cannot {action} job {record['id']}: "
                f"state is {record['state']!r}, not 'awaiting-confirmation'"
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
