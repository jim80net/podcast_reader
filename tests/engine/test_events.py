"""Tests for podcast_reader.engine.events (the public event-publish seam).

Per S6: the SSE fan-out is a public seam (:class:`EventBus`) constructed in
``serve_engine`` and shared by the job store and the pack manager — mypy
strict forbids the pack manager reaching into job-store privates. The
bounded-queue/pruning behavior itself is exercised through the job store's
delegating surface in test_jobs.py; here: the seam contract.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from podcast_reader.engine.events import (
    SUBSCRIBER_FULL_STREAK_LIMIT,
    SUBSCRIBER_QUEUE_SIZE,
    EventBus,
)
from podcast_reader.engine.jobs import JobStore
from podcast_reader.types import PipelineEvent, PipelineResult

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from podcast_reader.types import EventKind, JobRecord


def _event(kind: EventKind = "warning", message: str = "m") -> PipelineEvent:
    return PipelineEvent(kind=kind, step=None, message=message, data={})


class TestEventBus:
    def test_publish_reaches_all_subscribers(self) -> None:
        bus = EventBus()
        first, second = bus.subscribe(), bus.subscribe()
        bus.publish(_event(message="hello"))
        assert first.get_nowait()["message"] == "hello"
        assert second.get_nowait()["message"] == "hello"

    def test_unsubscribe_is_idempotent(self) -> None:
        bus = EventBus()
        q = bus.subscribe()
        bus.unsubscribe(q)
        bus.unsubscribe(q)
        assert bus.subscriber_count == 0

    def test_full_queue_drops_oldest(self) -> None:
        bus = EventBus()
        q = bus.subscribe()
        for i in range(SUBSCRIBER_QUEUE_SIZE + 1):
            bus.publish(_event(message=str(i)))
        assert q.qsize() == SUBSCRIBER_QUEUE_SIZE
        assert q.get_nowait()["message"] == "1"  # event 0 was dropped

    def test_abandoned_subscriber_pruned_after_full_streak(self) -> None:
        bus = EventBus()
        bus.subscribe()  # never drained
        for _ in range(SUBSCRIBER_QUEUE_SIZE + SUBSCRIBER_FULL_STREAK_LIMIT):
            bus.publish(_event())
        assert bus.subscriber_count == 0


class TestSharedBus:
    def test_job_store_publishes_into_an_injected_bus(self, tmp_path: Path) -> None:
        """The seam: a bus constructed outside the store carries job events,
        so a second producer (the pack manager) shares the same fan-out."""

        def runner(record: JobRecord, on_event: Callable[[PipelineEvent], None]) -> PipelineResult:
            on_event(_event(kind="job_done", message="done"))
            return PipelineResult(json_path="j", chapters_path=None, html_path="h", title="t")

        bus = EventBus()
        store = JobStore(tmp_path, runner, bus=bus)
        q = bus.subscribe()
        store.start_worker()
        try:
            store.submit("https://example.com/a", None)
            event = q.get(timeout=10)
            assert event["kind"] == "job_done"
            # an external producer publishes through the same seam
            bus.publish(_event(kind="pack_progress", message="pack"))
            assert q.get(timeout=10)["kind"] == "pack_progress"
        finally:
            store.shutdown()

    def test_store_exposes_its_bus(self, tmp_path: Path) -> None:
        bus = EventBus()
        store = JobStore(tmp_path, lambda record, on_event: None, bus=bus)  # type: ignore[arg-type,return-value]
        assert store.bus is bus

    def test_store_without_bus_creates_one(self, tmp_path: Path) -> None:
        store = JobStore(tmp_path, lambda record, on_event: None)  # type: ignore[arg-type,return-value]
        assert isinstance(store.bus, EventBus)


class TestEventKindWidening:
    def test_pack_and_progress_kinds_are_valid_pipeline_events(self) -> None:
        """Task 2.3: EventKind widens to the pack kinds plus step_progress;
        StepName gains diarize (for groups 3/5)."""
        kinds: tuple[EventKind, ...] = ("pack_state", "pack_progress", "step_progress")
        for kind in kinds:
            event = PipelineEvent(kind=kind, step=None, message="", data={})
            assert event["kind"] == kind
        diarize = PipelineEvent(kind="step_started", step="diarize", message="", data={})
        assert diarize["step"] == "diarize"
