"""Tests for podcast_reader.engine.jobs."""

from __future__ import annotations

import json
import threading
import time
from typing import TYPE_CHECKING

import pytest

from podcast_reader.engine.jobs import JobStore
from podcast_reader.pipeline import PipelineError
from podcast_reader.types import JOB_STATES, PipelineEvent, PipelineResult

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from podcast_reader.types import JobRecord

_RESULT = PipelineResult(
    json_path="/lib/aaa/a.json",
    chapters_path=None,
    html_path="/lib/aaa/a.html",
    title="Title",
)


def _ok_runner(record: JobRecord, on_event: Callable[[PipelineEvent], None]) -> PipelineResult:
    on_event(PipelineEvent(kind="step_started", step="resolve", message="Resolving...", data={}))
    on_event(PipelineEvent(kind="job_done", step=None, message="Done", data={}))
    return _RESULT


def _wait_for(predicate: Callable[[], bool], timeout: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


@pytest.fixture
def store(tmp_path: Path) -> JobStore:
    return JobStore(tmp_path, _ok_runner)


class TestSubmit:
    def test_submit_returns_queued_record(self, store: JobStore) -> None:
        record = store.submit("https://example.com/a", "Title")
        assert record["state"] == "queued"
        assert record["source"] == "https://example.com/a"
        assert record["title"] == "Title"
        assert record["events"] == []
        assert record["error"] is None
        assert record["created_at"] > 0
        assert record["id"]

    def test_awaiting_confirmation_not_reachable_via_submit(self, store: JobStore) -> None:
        # the state exists in the machine (forward compatibility)...
        assert "awaiting-confirmation" in JOB_STATES
        # ...but no submission path produces it
        for source in ("https://example.com/a", "https://example.com/b"):
            assert store.submit(source, None)["state"] == "queued"

    def test_submit_persists_to_journal(self, store: JobStore, tmp_path: Path) -> None:
        record = store.submit("https://example.com/a", None)
        journal = json.loads((tmp_path / "jobs.json").read_text())
        assert [r["id"] for r in journal] == [record["id"]]


class TestExecution:
    def test_successful_job_reaches_done_with_events(self, store: JobStore) -> None:
        record = store.submit("https://example.com/a", None)
        store.start_worker()
        assert _wait_for(lambda: store.get(record["id"])["state"] == "done")
        done = store.get(record["id"])
        assert done["result"] == _RESULT
        kinds = [e["kind"] for e in done["events"]]
        assert "step_started" in kinds
        assert kinds[-1] == "job_done"
        # events are tagged with the owning job id
        assert all(e["data"]["job_id"] == record["id"] for e in done["events"])
        store.shutdown()

    def test_failed_carries_structured_error(self, tmp_path: Path) -> None:
        def failing(record: JobRecord, on_event: Callable[[PipelineEvent], None]) -> PipelineResult:
            raise PipelineError("not_found", "File not found: /nope", "Check the path.")

        store = JobStore(tmp_path, failing)
        record = store.submit("/nope", None)
        store.start_worker()
        assert _wait_for(lambda: store.get(record["id"])["state"] == "failed")
        failed = store.get(record["id"])
        assert failed["error"] == {
            "code": "not_found",
            "message": "File not found: /nope",
            "hint": "Check the path.",
        }
        assert failed["events"][-1]["kind"] == "job_failed"
        store.shutdown()

    def test_unexpected_exception_maps_to_internal_error(self, tmp_path: Path) -> None:
        def exploding(
            record: JobRecord, on_event: Callable[[PipelineEvent], None]
        ) -> PipelineResult:
            raise RuntimeError("boom")

        store = JobStore(tmp_path, exploding)
        record = store.submit("https://example.com/a", None)
        store.start_worker()
        assert _wait_for(lambda: store.get(record["id"])["state"] == "failed")
        error = store.get(record["id"])["error"]
        assert error is not None
        assert error["code"] == "internal"
        assert "boom" in error["message"]
        store.shutdown()

    def test_fifo_single_worker(self, tmp_path: Path) -> None:
        release = threading.Event()
        first_started = threading.Event()
        run_order: list[str] = []

        def blocking(
            record: JobRecord, on_event: Callable[[PipelineEvent], None]
        ) -> PipelineResult:
            run_order.append(record["source"])
            first_started.set()
            assert release.wait(timeout=10)
            return _RESULT

        store = JobStore(tmp_path, blocking)
        store.start_worker()
        first = store.submit("https://example.com/first", None)
        second = store.submit("https://example.com/second", None)
        assert first_started.wait(timeout=10)
        assert store.get(first["id"])["state"] == "running"
        assert store.get(second["id"])["state"] == "queued"
        release.set()
        assert _wait_for(lambda: store.get(second["id"])["state"] == "done")
        assert store.get(first["id"])["state"] == "done"
        assert run_order == ["https://example.com/first", "https://example.com/second"]
        store.shutdown()


class TestPersistence:
    def test_transitions_persist_across_reload(self, tmp_path: Path) -> None:
        store = JobStore(tmp_path, _ok_runner)
        record = store.submit("https://example.com/a", None)
        store.start_worker()
        assert _wait_for(lambda: store.get(record["id"])["state"] == "done")
        store.shutdown()

        reloaded = JobStore(tmp_path, _ok_runner)
        seen = reloaded.get(record["id"])
        assert seen["state"] == "done"
        assert seen["result"] == _RESULT
        assert [e["kind"] for e in seen["events"]][-1] == "job_done"

    def test_startup_marks_running_as_interrupted(self, tmp_path: Path) -> None:
        journal = [
            {
                "id": "j-running",
                "source": "https://example.com/a",
                "title": None,
                "state": "running",
                "error": None,
                "events": [],
                "result": None,
                "created_at": 1.0,
                "updated_at": 1.0,
            },
            {
                "id": "j-done",
                "source": "https://example.com/b",
                "title": None,
                "state": "done",
                "error": None,
                "events": [],
                "result": None,
                "created_at": 1.0,
                "updated_at": 1.0,
            },
        ]
        (tmp_path / "jobs.json").write_text(json.dumps(journal))

        store = JobStore(tmp_path, _ok_runner)
        assert store.get("j-running")["state"] == "interrupted"
        assert store.get("j-done")["state"] == "done"
        # the flip is journaled, not just in memory
        on_disk = json.loads((tmp_path / "jobs.json").read_text())
        assert {r["id"]: r["state"] for r in on_disk}["j-running"] == "interrupted"

    def test_retry_by_resubmission(self, tmp_path: Path) -> None:
        journal = [
            {
                "id": "j-old",
                "source": "https://example.com/a",
                "title": None,
                "state": "interrupted",
                "error": None,
                "events": [],
                "result": None,
                "created_at": 1.0,
                "updated_at": 1.0,
            }
        ]
        (tmp_path / "jobs.json").write_text(json.dumps(journal))

        store = JobStore(tmp_path, _ok_runner)
        retry = store.submit("https://example.com/a", None)
        assert retry["id"] != "j-old"
        assert retry["state"] == "queued"
        assert store.get("j-old")["state"] == "interrupted"
        assert len(store.list_jobs()) == 2


class TestSubscriptions:
    def test_subscriber_receives_events(self, tmp_path: Path) -> None:
        store = JobStore(tmp_path, _ok_runner)
        q = store.subscribe()
        record = store.submit("https://example.com/a", None)
        store.start_worker()
        event = q.get(timeout=10)
        assert event["data"]["job_id"] == record["id"]
        store.unsubscribe(q)
        assert store.subscriber_count == 0
        store.shutdown()

    def test_full_subscriber_queue_drops_oldest(self, tmp_path: Path) -> None:
        def chatty(record: JobRecord, on_event: Callable[[PipelineEvent], None]) -> PipelineResult:
            for i in range(300):
                on_event(PipelineEvent(kind="warning", step=None, message=str(i), data={}))
            return _RESULT

        store = JobStore(tmp_path, chatty)
        q = store.subscribe()
        record = store.submit("https://example.com/a", None)
        store.start_worker()
        assert _wait_for(lambda: store.get(record["id"])["state"] == "done")
        assert q.full()
        first = q.get(timeout=1)
        # oldest events were dropped, so the queue no longer starts at "0"
        assert first["message"] != "0"
        store.shutdown()
