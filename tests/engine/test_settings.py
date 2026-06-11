"""Tests for podcast_reader.engine.settings."""

from __future__ import annotations

import json
import stat
from typing import TYPE_CHECKING

from podcast_reader.engine.settings import (
    data_dir,
    load_engine_state,
    load_settings,
    save_engine_state,
    save_settings,
    token_fingerprint,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


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
        assert settings["chapter_model"] == "claude-haiku-4-5-20251001"
        assert settings["library_dir"] == str(tmp_path / "library")

    def test_defaults_not_persisted_until_saved(self, tmp_path: Path) -> None:
        load_settings(tmp_path)
        assert not (tmp_path / "settings.json").exists()


class TestTokenFingerprint:
    def test_fingerprint_is_sha256_prefix(self) -> None:
        import hashlib

        token = "secret-token"
        expected = hashlib.sha256(token.encode()).hexdigest()[:16]
        assert token_fingerprint(token) == expected
