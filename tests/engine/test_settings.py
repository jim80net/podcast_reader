"""Tests for podcast_reader.engine.settings."""

from __future__ import annotations

import json
import logging
import os
import stat
import sys
from typing import TYPE_CHECKING

import pytest

from podcast_reader.engine.settings import (
    EngineState,
    data_dir,
    load_engine_state,
    load_settings,
    save_engine_state,
    save_settings,
    token_fingerprint,
)

if TYPE_CHECKING:
    from pathlib import Path


class TestDataDir:
    def test_data_dir_env_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        target = tmp_path / "custom-data"
        monkeypatch.setenv("PODCAST_READER_DATA_DIR", str(target))
        assert data_dir() == target
        assert target.is_dir()

    def test_data_dir_default_under_home(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("PODCAST_READER_DATA_DIR", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        assert data_dir() == tmp_path / "PodcastReader"

    def test_data_dir_env_tilde_expanded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("PODCAST_READER_DATA_DIR", "~/custom-data")
        assert data_dir() == tmp_path / "custom-data"
        assert (tmp_path / "custom-data").is_dir()


class TestEngineState:
    def test_engine_state_created_0600(self, tmp_path: Path) -> None:
        state = load_engine_state(tmp_path)
        assert state["port"] == 0
        assert len(state["token"]) >= 32
        state_file = tmp_path / "engine-state.json"
        assert state_file.exists()
        assert stat.S_IMODE(state_file.stat().st_mode) == 0o600

    def test_engine_state_reused(self, tmp_path: Path) -> None:
        first = load_engine_state(tmp_path)
        second = load_engine_state(tmp_path)
        assert second == first

    def test_save_engine_state_persists_port(self, tmp_path: Path) -> None:
        state = load_engine_state(tmp_path)
        state["port"] = 4242
        save_engine_state(tmp_path, state)
        assert load_engine_state(tmp_path)["port"] == 4242
        assert stat.S_IMODE((tmp_path / "engine-state.json").stat().st_mode) == 0o600

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX mode bits are a no-op on Windows")
    def test_permissive_preexisting_state_rehardened_on_load(self, tmp_path: Path) -> None:
        """A pre-existing engine-state.json with permissive mode is chmod'd
        back to 0600 when loaded (D2)."""
        state_file = tmp_path / "engine-state.json"
        state_file.write_text(json.dumps({"port": 4242, "token": "t" * 43}))
        state_file.chmod(0o644)

        state = load_engine_state(tmp_path)

        assert state == {"port": 4242, "token": "t" * 43}
        assert stat.S_IMODE(state_file.stat().st_mode) == 0o600


class TestSecureWrites:
    def test_engine_state_tmp_created_0600_at_open(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The temp file must carry mode 0600 from the moment it exists
        (O_CREAT|O_EXCL with the mode at open) — no chmod-later window."""
        observed: list[tuple[bool, int]] = []
        real_open = os.open

        def spy(path: str, flags: int, mode: int = 0o777) -> int:
            fd = real_open(path, flags, mode)
            observed.append((bool(flags & os.O_EXCL), stat.S_IMODE(os.fstat(fd).st_mode)))
            return fd

        monkeypatch.setattr("podcast_reader.engine.settings.os.open", spy)
        save_engine_state(tmp_path, EngineState(port=1, token="t" * 43))
        assert observed == [(True, 0o600)]
        assert stat.S_IMODE((tmp_path / "engine-state.json").stat().st_mode) == 0o600

    def test_stale_tmp_file_does_not_break_secure_write(self, tmp_path: Path) -> None:
        """A temp file left by a crash must not trip O_EXCL on the next write."""
        (tmp_path / "engine-state.json.tmp").write_text("stale")
        save_engine_state(tmp_path, EngineState(port=7, token="t" * 43))
        assert load_engine_state(tmp_path)["port"] == 7
        assert list(tmp_path.glob("*.tmp")) == []


class TestUserSettings:
    def test_user_settings_roundtrip_atomic(self, tmp_path: Path) -> None:
        settings = load_settings(tmp_path)
        settings["whisper_device"] = "cpu"
        settings["sentences"] = 3
        save_settings(tmp_path, settings)
        assert load_settings(tmp_path) == settings
        # atomic write leaves no temp file behind
        assert list(tmp_path.glob("*.tmp")) == []
        # the settings file itself is valid JSON
        assert json.loads((tmp_path / "settings.json").read_text()) == dict(settings)

    def test_default_settings_match_env_defaults(self, tmp_path: Path) -> None:
        settings = load_settings(tmp_path)
        assert settings["whisper_model"] == "large-v3"
        assert settings["whisper_lang"] == "en"
        assert settings["whisper_device"] == "cuda"
        assert settings["sentences"] == 5
        # "" means "the provider's default model" (multi-provider-chapters).
        assert settings["chapter_model"] == ""
        assert settings["chapter_provider"] == "anthropic"
        assert settings["custom_provider_url"] == ""
        assert settings["library_dir"] == str(tmp_path / "library")
        # diarization-worker spec: disabled by default.
        assert settings["diarize"] is False

    def test_defaults_not_persisted_until_saved(self, tmp_path: Path) -> None:
        load_settings(tmp_path)
        assert not (tmp_path / "settings.json").exists()

    def test_stale_phase1_settings_file_loads_with_defaults_merged(self, tmp_path: Path) -> None:
        """Spec scenario: Phase 1 settings file loads — new fields get defaults,
        existing fields are preserved; no KeyError can reach the job runner.

        M3: Phase 1 persisted chapter_model="claude-haiku-4-5-20251001"
        explicitly (it was the only provider). A file lacking chapter_provider
        with exactly that model is the Phase 1 fingerprint: the model is
        normalized to "" (provider default) so a later provider switch never
        sends an Anthropic model id to another provider."""
        phase1 = {
            "whisper_model": "medium",
            "whisper_lang": "en",
            "whisper_device": "cpu",
            "sentences": 3,
            "library_dir": str(tmp_path / "library"),
            "chapter_model": "claude-haiku-4-5-20251001",
        }
        (tmp_path / "settings.json").write_text(json.dumps(phase1))

        settings = load_settings(tmp_path)

        assert settings["chapter_provider"] == "anthropic"
        assert settings["custom_provider_url"] == ""
        assert settings["whisper_model"] == "medium"  # file values win over defaults
        assert settings["sentences"] == 3
        assert settings["chapter_model"] == ""  # normalized to "provider default"

    def test_pre_diarize_file_upgrades_to_diarize_false(self, tmp_path: Path) -> None:
        """diarization-worker spec: settings files predating the `diarize`
        field upgrade cleanly via the merge-over-defaults discipline."""
        pre_diarize = {
            "whisper_model": "medium",
            "whisper_lang": "en",
            "whisper_device": "cpu",
            "sentences": 3,
            "library_dir": str(tmp_path / "library"),
            "chapter_model": "",
            "chapter_provider": "anthropic",
            "custom_provider_url": "",
        }
        (tmp_path / "settings.json").write_text(json.dumps(pre_diarize))

        settings = load_settings(tmp_path)

        assert settings["diarize"] is False
        assert settings["whisper_model"] == "medium"  # file values still win

    def test_post_upgrade_file_keeps_explicit_haiku_model(self, tmp_path: Path) -> None:
        """M3: a file WITH chapter_provider chose that model deliberately —
        it is preserved verbatim, not normalized."""
        upgraded = {
            "whisper_model": "medium",
            "whisper_lang": "en",
            "whisper_device": "cpu",
            "sentences": 3,
            "library_dir": str(tmp_path / "library"),
            "chapter_model": "claude-haiku-4-5-20251001",
            "chapter_provider": "anthropic",
            "custom_provider_url": "",
        }
        (tmp_path / "settings.json").write_text(json.dumps(upgraded))

        assert load_settings(tmp_path)["chapter_model"] == "claude-haiku-4-5-20251001"

    def test_phase1_file_with_custom_model_preserved(self, tmp_path: Path) -> None:
        """M3: a Phase 1 file whose model differs from the installed default
        was a deliberate user choice — preserved verbatim."""
        phase1 = {
            "whisper_model": "medium",
            "whisper_lang": "en",
            "whisper_device": "cpu",
            "sentences": 3,
            "library_dir": str(tmp_path / "library"),
            "chapter_model": "claude-opus-x",
        }
        (tmp_path / "settings.json").write_text(json.dumps(phase1))

        assert load_settings(tmp_path)["chapter_model"] == "claude-opus-x"


class TestCorruptSettings:
    def test_garbage_settings_quarantined_and_defaults_returned(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Malformed settings.json must not crash the engine (D4): quarantine
        to settings.json.corrupt, warn, and fall back to defaults."""
        (tmp_path / "settings.json").write_text("{ not json at all")

        with caplog.at_level(logging.WARNING, logger="podcast_reader.engine.settings"):
            settings = load_settings(tmp_path)

        assert settings == load_settings(tmp_path)  # defaults, stable
        assert settings["whisper_model"] == "large-v3"
        assert (tmp_path / "settings.json.corrupt").read_text() == "{ not json at all"
        assert not (tmp_path / "settings.json").exists()
        assert "settings.json.corrupt" in caplog.text
        # the store remains fully usable: a save round-trips again
        save_settings(tmp_path, settings)
        assert load_settings(tmp_path) == settings

    def test_wrong_shape_settings_treated_as_corrupt(self, tmp_path: Path) -> None:
        (tmp_path / "settings.json").write_text('["not", "an", "object"]')
        settings = load_settings(tmp_path)
        assert settings["whisper_model"] == "large-v3"
        assert (tmp_path / "settings.json.corrupt").exists()

    def test_quarantine_rename_failure_logged_and_defaults_returned(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        import pathlib

        (tmp_path / "settings.json").write_text("{ not json at all")

        def deny_replace(self: pathlib.Path, target: object) -> pathlib.Path:
            raise OSError("read-only data dir")

        monkeypatch.setattr(pathlib.Path, "replace", deny_replace)
        with caplog.at_level(logging.WARNING, logger="podcast_reader.engine.settings"):
            settings = load_settings(tmp_path)
        assert settings["whisper_model"] == "large-v3"
        assert "read-only data dir" in caplog.text


class TestTokenFingerprint:
    def test_fingerprint_is_sha256_prefix(self) -> None:
        import hashlib

        token = "secret-token"
        expected = hashlib.sha256(token.encode()).hexdigest()[:16]
        assert token_fingerprint(token) == expected
