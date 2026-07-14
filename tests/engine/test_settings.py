"""Tests for podcast_reader.engine.settings."""

from __future__ import annotations

import json
import logging
import os
import stat
import sys
from types import SimpleNamespace
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
    def test_windows_private_sddl_sets_explicit_user_owner_and_only_two_aces(self) -> None:
        from podcast_reader.engine.settings import (
            _windows_dacl_principals_are_exact,
            _windows_private_sddl,
        )

        sid = "S-1-5-21-100-200-300-400"
        assert _windows_private_sddl(sid) == (f"O:{sid}D:P(A;;FA;;;SY)(A;;FA;;;{sid})")

        resolved = {"SY": "S-1-5-18", "LA": "S-1-5-21-local-500"}

        def equals(principal: str, expected: str) -> bool:
            return resolved.get(principal, principal) == expected

        assert _windows_dacl_principals_are_exact({"SY", "LA"}, "S-1-5-21-local-500", equals)
        assert not _windows_dacl_principals_are_exact({"SY", "LA"}, "S-1-5-21-domain-500", equals)

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

    def test_windows_dacl_is_applied_before_replace_and_verified_after(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from podcast_reader.engine import settings as settings_module

        events: list[tuple[str, str]] = []
        real_open = os.open
        real_replace = os.replace
        real_write = settings_module._write_text_to_fd

        monkeypatch.setattr("podcast_reader.engine.settings.sys.platform", "win32")

        def observed_create(path: Path) -> int:
            fd = real_open(path, flags, mode)
            events.append(("create", path.name))
            events.append(("apply", path.name))
            events.append(("verify", path.name))
            return fd

        def observed_write(fd: int, data: str) -> None:
            events.append(("write", "engine-state.json.tmp"))
            real_write(fd, data)

        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        mode = 0o600
        monkeypatch.setattr(
            "podcast_reader.engine.settings._windows_create_private_fd", observed_create
        )
        monkeypatch.setattr(
            "podcast_reader.engine.settings.verify_windows_private_file",
            lambda path: events.append(("verify", path.name)),
        )
        monkeypatch.setattr("podcast_reader.engine.settings._write_text_to_fd", observed_write)

        def observed_replace(source: Path, destination: Path) -> None:
            events.append(("replace", destination.name))
            real_replace(source, destination)

        monkeypatch.setattr("podcast_reader.engine.settings.os.replace", observed_replace)
        save_engine_state(tmp_path, EngineState(port=1, token="t" * 43))

        assert events == [
            ("create", "engine-state.json.tmp"),
            ("apply", "engine-state.json.tmp"),
            ("verify", "engine-state.json.tmp"),
            ("write", "engine-state.json.tmp"),
            ("replace", "engine-state.json"),
            ("verify", "engine-state.json"),
        ]

    def test_windows_dacl_failure_leaves_no_secret_temp(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        secret = "never-written-before-protection"
        monkeypatch.setattr("podcast_reader.engine.settings.sys.platform", "win32")

        def reject_dacl(_path: Path) -> None:
            raise PermissionError("DACL rejected")

        monkeypatch.setattr(
            "podcast_reader.engine.settings._windows_create_private_fd", reject_dacl
        )
        with pytest.raises(PermissionError, match="DACL rejected"):
            save_engine_state(tmp_path, EngineState(port=1, token=secret))

        assert not (tmp_path / "engine-state.json").exists()
        assert not (tmp_path / "engine-state.json.tmp").exists()
        assert all(secret not in path.read_text(errors="replace") for path in tmp_path.rglob("*"))

    def test_windows_post_replace_verification_failure_removes_destination(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from podcast_reader.engine import settings as settings_module

        secret = "remove-unverified-destination"
        monkeypatch.setattr("podcast_reader.engine.settings.sys.platform", "win32")

        def create_protected_temp(path: Path) -> int:
            assert path.name.endswith(".tmp")
            return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)

        def reject_destination(path: Path) -> None:
            if not path.name.endswith(".tmp"):
                raise PermissionError("final DACL unverified")

        monkeypatch.setattr(
            "podcast_reader.engine.settings._windows_create_private_fd", create_protected_temp
        )
        monkeypatch.setattr(
            "podcast_reader.engine.settings.verify_windows_private_file", reject_destination
        )
        # The mocked ensure still represents its internal temp verification;
        # the separate verifier is the post-replace boundary under test.
        assert settings_module.sys.platform == "win32"
        with pytest.raises(PermissionError, match="final DACL unverified"):
            save_engine_state(tmp_path, EngineState(port=1, token=secret))

        assert not (tmp_path / "engine-state.json").exists()
        assert not (tmp_path / "engine-state.json.tmp").exists()

    def test_windows_handle_conversion_failure_closes_handle_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import ctypes

        from podcast_reader.engine import settings as settings_module

        closed: list[int] = []

        class Function:
            argtypes: object = None
            restype: object = None

            def __call__(self, *_args: object) -> int:
                return 1234

        def close_handle(handle: int) -> None:
            closed.append(handle)

        def local_free(_pointer: object) -> None:
            return

        kernel = SimpleNamespace(
            CreateFileW=Function(), CloseHandle=close_handle, LocalFree=local_free
        )

        class Msvcrt:
            @staticmethod
            def open_osfhandle(_handle: int, _flags: int) -> int:
                raise OSError("conversion failed")

        monkeypatch.setattr(settings_module, "msvcrt", Msvcrt())
        monkeypatch.setattr(
            settings_module,
            "_windows_private_descriptor",
            lambda: (object(), kernel, ctypes.c_void_p(99)),
        )
        monkeypatch.setattr(settings_module, "_verify_windows_private_handle", lambda _handle: None)

        with pytest.raises(OSError, match="conversion failed"):
            settings_module._windows_create_private_fd(tmp_path / "state.tmp")

        assert closed == [1234]

    @pytest.mark.skipif(sys.platform != "win32", reason="requires the Windows security APIs")
    def test_windows_engine_state_has_verified_owner_system_dacl(self, tmp_path: Path) -> None:
        from podcast_reader.engine.settings import verify_windows_private_file

        load_engine_state(tmp_path)
        verify_windows_private_file(tmp_path / "engine-state.json")


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
        assert settings["custom_providers"] == []
        assert settings["library_dir"] == str(tmp_path / "library")
        # diarization-worker spec: disabled by default.
        assert settings["diarize"] is False
        assert settings["caption_cleanup"] is False
        # media-playback spec: lazy media cache cap, 5 GiB default.
        assert settings["media_cache_max_bytes"] == 5 * 1024**3

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
        assert settings["caption_cleanup"] is False
        assert settings["whisper_model"] == "medium"  # file values still win

    def test_pre_media_cache_file_upgrades_to_default(self, tmp_path: Path) -> None:
        """media-playback spec: settings files predating `media_cache_max_bytes`
        upgrade cleanly via the merge-over-defaults discipline."""
        pre_media = {
            "whisper_model": "medium",
            "whisper_lang": "en",
            "whisper_device": "cpu",
            "sentences": 3,
            "library_dir": str(tmp_path / "library"),
            "chapter_model": "",
            "chapter_provider": "anthropic",
            "custom_provider_url": "",
            "diarize": False,
        }
        (tmp_path / "settings.json").write_text(json.dumps(pre_media))

        settings = load_settings(tmp_path)

        assert settings["media_cache_max_bytes"] == 5 * 1024**3
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

    def test_named_provider_roundtrip_is_canonical_and_nonsecret(self, tmp_path: Path) -> None:
        settings = load_settings(tmp_path)
        settings["custom_providers"] = [
            {
                "name": "office-gateway",
                "base_url": "https://llm.corp.example/v1",
                "default_model": "corp-small",
                "max_tokens": 32768,
            }
        ]

        save_settings(tmp_path, settings)

        loaded = load_settings(tmp_path)
        assert loaded["custom_providers"] == settings["custom_providers"]
        persisted = (tmp_path / "settings.json").read_text()
        assert "api_key" not in persisted
        assert "PODCAST_READER_PROVIDER_OFFICE_GATEWAY_KEY" not in persisted


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

    @pytest.mark.parametrize(
        "providers",
        [
            "not-a-list",
            [{"name": "broken"}],
            [
                {
                    "name": "office-gateway",
                    "base_url": "https://user:secret@llm.example/v1",
                    "default_model": "corp-small",
                    "max_tokens": 100,
                }
            ],
            [
                {
                    "name": "office-gateway",
                    "base_url": "https://llm.example/v1",
                    "default_model": "corp-small",
                    "max_tokens": 100,
                    "api_key": "must-not-survive",
                }
            ],
        ],
    )
    def test_malformed_named_providers_quarantined(self, tmp_path: Path, providers: object) -> None:
        payload = dict(load_settings(tmp_path))
        payload["custom_providers"] = providers
        (tmp_path / "settings.json").write_text(json.dumps(payload))

        settings = load_settings(tmp_path)

        assert settings["custom_providers"] == []
        assert not (tmp_path / "settings.json").exists()
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
