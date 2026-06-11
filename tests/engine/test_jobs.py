"""Tests for podcast_reader.engine.jobs."""

from __future__ import annotations

import json
import logging
import os
import pathlib
import sys
import threading
import time
from typing import TYPE_CHECKING

import pytest

from podcast_reader.engine.jobs import (
    MAX_TERMINAL_JOBS,
    SUBSCRIBER_FULL_STREAK_LIMIT,
    SUBSCRIBER_QUEUE_SIZE,
    JobStore,
)
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

    def test_worker_survives_exception_escaping_run_job(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An exception escaping _run_job must not kill the worker loop (C3).

        The first job is marked failed{code: internal} best-effort and the
        second job still executes.
        """
        store = JobStore(tmp_path, _ok_runner)
        original_run_job = store._run_job
        crashed: list[str] = []

        def flaky_run_job(job_id: str) -> None:
            if not crashed:
                crashed.append(job_id)
                raise OSError("journal write exploded")
            original_run_job(job_id)

        store._run_job = flaky_run_job  # type: ignore[method-assign]
        first = store.submit("https://example.com/first", None)
        second = store.submit("https://example.com/second", None)
        with caplog.at_level(logging.ERROR, logger="podcast_reader.engine.jobs"):
            store.start_worker()
            assert _wait_for(lambda: store.get(second["id"])["state"] == "done")
        failed = store.get(first["id"])
        assert failed["state"] == "failed"
        assert failed["error"] is not None
        assert failed["error"]["code"] == "internal"
        assert "journal write exploded" in caplog.text
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


class TestShutdown:
    def test_shutdown_skips_backlog_and_recovery_reenqueues_it(self, tmp_path: Path) -> None:
        """shutdown() must not drain queued jobs first (C7).

        The worker finishes the running job, then exits without running the
        backlog; those jobs stay ``queued`` in the journal and a fresh
        JobStore on the same data_dir re-enqueues them.
        """
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
        third = store.submit("https://example.com/third", None)
        assert first_started.wait(timeout=10)

        shutdown_thread = threading.Thread(target=store.shutdown)
        shutdown_thread.start()  # blocks joining the worker mid-job
        # Only release the in-flight job once the stop flag is definitely set,
        # otherwise the worker could dequeue the backlog before it sees stop.
        assert _wait_for(store._stop.is_set)
        release.set()
        shutdown_thread.join(timeout=10)
        assert not shutdown_thread.is_alive()

        assert run_order == ["https://example.com/first"]
        assert store.get(first["id"])["state"] == "done"
        on_disk = {r["id"]: r["state"] for r in json.loads((tmp_path / "jobs.json").read_text())}
        assert on_disk[second["id"]] == "queued"
        assert on_disk[third["id"]] == "queued"

        # H2 recovery: a fresh store on the same data_dir re-enqueues the backlog
        recovered = JobStore(tmp_path, _ok_runner)
        recovered.start_worker()
        assert _wait_for(lambda: recovered.get(third["id"])["state"] == "done")
        assert recovered.get(second["id"])["state"] == "done"
        recovered.shutdown()


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

    def test_restart_reenqueues_queued_jobs_in_created_order(self, tmp_path: Path) -> None:
        """Persisted queued jobs must run after a restart (FIFO by created_at),
        while the interrupted running job stays interrupted."""

        def _record(job_id: str, state: str, created_at: float) -> dict[str, object]:
            return {
                "id": job_id,
                "source": f"https://example.com/{job_id}",
                "title": None,
                "state": state,
                "error": None,
                "events": [],
                "result": None,
                "created_at": created_at,
                "updated_at": created_at,
            }

        # journal order deliberately disagrees with created_at order
        journal = [
            _record("j-running", "running", 1.0),
            _record("j-queued-late", "queued", 3.0),
            _record("j-queued-early", "queued", 2.0),
        ]
        (tmp_path / "jobs.json").write_text(json.dumps(journal))

        run_order: list[str] = []

        def ordered_runner(
            record: JobRecord, on_event: Callable[[PipelineEvent], None]
        ) -> PipelineResult:
            run_order.append(record["id"])
            return _RESULT

        store = JobStore(tmp_path, ordered_runner)
        assert store.get("j-running")["state"] == "interrupted"
        store.start_worker()
        assert _wait_for(lambda: store.get("j-queued-late")["state"] == "done")
        assert store.get("j-queued-early")["state"] == "done"
        assert store.get("j-running")["state"] == "interrupted"
        assert run_order == ["j-queued-early", "j-queued-late"]
        store.shutdown()

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

    def test_subscriber_full_for_streak_limit_is_pruned(self, tmp_path: Path) -> None:
        """A queue that stays full for SUBSCRIBER_FULL_STREAK_LIMIT consecutive
        publishes is treated as abandoned and dropped (no GC-dependent cleanup)."""
        store = JobStore(tmp_path, _ok_runner)
        store.subscribe()  # never drained
        event = PipelineEvent(kind="warning", step=None, message="m", data={})
        for _ in range(SUBSCRIBER_QUEUE_SIZE):  # fill without ever hitting Full
            store._publish(event)
        assert store.subscriber_count == 1
        for _ in range(SUBSCRIBER_FULL_STREAK_LIMIT - 1):
            store._publish(event)
        assert store.subscriber_count == 1  # one publish short of the limit
        store._publish(event)
        assert store.subscriber_count == 0

    def test_full_streak_resets_when_consumer_drains(self, tmp_path: Path) -> None:
        store = JobStore(tmp_path, _ok_runner)
        q = store.subscribe()
        event = PipelineEvent(kind="warning", step=None, message="m", data={})
        for _ in range(SUBSCRIBER_QUEUE_SIZE + SUBSCRIBER_FULL_STREAK_LIMIT - 1):
            store._publish(event)
        q.get_nowait()  # a live consumer drains → the next publish is not full
        store._publish(event)
        assert store.subscriber_count == 1
        # only a fresh full streak of LIMIT prunes it
        for _ in range(SUBSCRIBER_FULL_STREAK_LIMIT):
            store._publish(event)
        assert store.subscriber_count == 0


def _journal_record(job_id: str, state: str, stamp: float) -> dict[str, object]:
    return {
        "id": job_id,
        "source": f"https://example.com/{job_id}",
        "title": None,
        "state": state,
        "error": None,
        "events": [],
        "result": None,
        "created_at": stamp,
        "updated_at": stamp,
    }


class TestJournalRetention:
    def test_terminal_jobs_capped_keeping_most_recent_and_all_non_terminal(
        self, tmp_path: Path
    ) -> None:
        journal = [_journal_record(f"j{i}", "done", float(i)) for i in range(MAX_TERMINAL_JOBS + 5)]
        journal.append(_journal_record("j-queued-old", "queued", 0.5))  # oldest, but kept
        (tmp_path / "jobs.json").write_text(json.dumps(journal))

        store = JobStore(tmp_path, _ok_runner)
        store.submit("https://example.com/new", None)  # any write applies the cap

        on_disk = json.loads((tmp_path / "jobs.json").read_text())
        terminal = [r["id"] for r in on_disk if r["state"] == "done"]
        assert len(terminal) == MAX_TERMINAL_JOBS
        # the oldest terminal jobs (by updated_at) were dropped...
        assert "j0" not in terminal
        assert f"j{MAX_TERMINAL_JOBS + 4}" in terminal
        # ...while non-terminal jobs survive regardless of age
        states = {r["id"]: r["state"] for r in on_disk}
        assert states["j-queued-old"] == "queued"

    def test_no_pruning_below_cap(self, tmp_path: Path) -> None:
        journal = [_journal_record("j-done", "done", 1.0)]
        (tmp_path / "jobs.json").write_text(json.dumps(journal))
        store = JobStore(tmp_path, _ok_runner)
        submitted = store.submit("https://example.com/new", None)
        on_disk = json.loads((tmp_path / "jobs.json").read_text())
        assert {r["id"] for r in on_disk} == {"j-done", submitted["id"]}


class TestCorruptJournal:
    def test_corrupt_journal_quarantined_and_store_starts_empty(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        (tmp_path / "jobs.json").write_text("{ not json at all")
        with caplog.at_level(logging.WARNING, logger="podcast_reader.engine.jobs"):
            store = JobStore(tmp_path, _ok_runner)
        assert store.list_jobs() == []
        assert (tmp_path / "jobs.json.corrupt").read_text() == "{ not json at all"
        assert not (tmp_path / "jobs.json").exists()
        assert "jobs.json.corrupt" in caplog.text
        # the store remains fully usable
        record = store.submit("https://example.com/a", None)
        assert [r["id"] for r in json.loads((tmp_path / "jobs.json").read_text())] == [record["id"]]

    def test_wrong_shape_journal_treated_as_corrupt(self, tmp_path: Path) -> None:
        (tmp_path / "jobs.json").write_text('{"not": "a list"}')
        store = JobStore(tmp_path, _ok_runner)
        assert store.list_jobs() == []
        assert (tmp_path / "jobs.json.corrupt").exists()

    @pytest.mark.skipif(
        sys.platform == "win32" or os.geteuid() == 0,
        reason="chmod 0o000 does not block reads on Windows or for root",
    )
    def test_unreadable_journal_quarantined_and_store_starts_empty(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An OSError reading jobs.json must not crash startup (C8)."""
        journal = tmp_path / "jobs.json"
        journal.write_text("[]")
        journal.chmod(0o000)
        try:
            with caplog.at_level(logging.WARNING, logger="podcast_reader.engine.jobs"):
                store = JobStore(tmp_path, _ok_runner)
        finally:
            for leftover in (journal, tmp_path / "jobs.json.corrupt"):
                if leftover.exists():
                    leftover.chmod(0o644)
        assert store.list_jobs() == []
        assert (tmp_path / "jobs.json.corrupt").exists()
        assert "jobs.json.corrupt" in caplog.text

    def test_quarantine_rename_failure_logged_and_store_starts_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A failing quarantine rename is logged, not raised (C8)."""
        (tmp_path / "jobs.json").write_text("{ not json at all")

        def deny_replace(self: pathlib.Path, target: object) -> pathlib.Path:
            raise OSError("read-only data dir")

        monkeypatch.setattr(pathlib.Path, "replace", deny_replace)
        with caplog.at_level(logging.WARNING, logger="podcast_reader.engine.jobs"):
            store = JobStore(tmp_path, _ok_runner)
        assert store.list_jobs() == []
        assert "quarantine" in caplog.text.lower()
        assert "read-only data dir" in caplog.text
