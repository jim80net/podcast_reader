"""Tests for podcast_reader.engine.cookies (Netscape jar validation + storage)."""

from __future__ import annotations

import logging
import stat
from typing import TYPE_CHECKING

import pytest

from podcast_reader.engine.cookies import (
    MAX_JAR_BYTES,
    CookieJarError,
    cookies_dir,
    delete_jar,
    jar_path,
    list_jars,
    resolve_jar,
    resolve_jar_for_source,
    store_jar,
    validate_jar,
)

if TYPE_CHECKING:
    from pathlib import Path

_HEADER = "# Netscape HTTP Cookie File"


def _line(domain: str, name: str = "session", value: str = "secret-cookie-value") -> str:
    return f"{domain}\tTRUE\t/\tTRUE\t1900000000\t{name}\t{value}"


_VALID_JAR = "\n".join([_HEADER, _line("example.com"), _line(".example.com", "auth")]) + "\n"


class TestValidateJar:
    def test_valid_jar_accepted(self) -> None:
        validate_jar("example.com", _VALID_JAR)

    def test_parent_domain_dot_strip_accepted(self) -> None:
        """Spec scenario (per U4): a `.example.com` cookie line matches its
        declared `example.com` jar — the leading dot is stripped first."""
        validate_jar("example.com", _line(".example.com"))

    def test_subdomain_cookie_line_accepted(self) -> None:
        validate_jar("example.com", _line("media.example.com"))

    def test_httponly_prefixed_line_accepted(self) -> None:
        validate_jar("example.com", "#HttpOnly_" + _line(".example.com"))

    def test_comment_and_blank_lines_skipped(self) -> None:
        validate_jar("example.com", f"{_HEADER}\n\n# a comment\n{_line('example.com')}\n")

    def test_foreign_domain_rejected(self) -> None:
        """Spec scenario: a cookie line for `other.org` in a jar declared for
        `example.com` is rejected — no smuggling jars for other sites."""
        jar = "\n".join([_line("example.com"), _line("other.org")])
        with pytest.raises(CookieJarError):
            validate_jar("example.com", jar)

    def test_suffix_match_requires_label_boundary(self) -> None:
        """`notexample.com` must not pass as a suffix of `example.com`."""
        with pytest.raises(CookieJarError):
            validate_jar("example.com", _line("notexample.com"))

    @pytest.mark.parametrize("domain", ["..example.com", "...example.com", ".", ".."])
    def test_multi_dot_cookie_domains_rejected(self, domain: str) -> None:
        """Per V2: a single leading-dot strip let `..example.com` slip through
        the suffix check — every leading dot is stripped and the remainder
        must still be a valid bare hostname."""
        with pytest.raises(CookieJarError):
            validate_jar("example.com", _line(domain))

    def test_httponly_foreign_domain_rejected(self) -> None:
        with pytest.raises(CookieJarError):
            validate_jar("example.com", "#HttpOnly_" + _line("other.org"))

    def test_malformed_jar_rejected(self) -> None:
        """Spec scenario: a body that does not parse as Netscape cookie lines
        is rejected."""
        with pytest.raises(CookieJarError):
            validate_jar("example.com", "this is not a cookie jar")

    def test_wrong_field_count_rejected(self) -> None:
        with pytest.raises(CookieJarError):
            validate_jar("example.com", "example.com\tTRUE\t/\tTRUE\t190\tname")

    def test_jar_without_cookie_lines_rejected(self) -> None:
        with pytest.raises(CookieJarError):
            validate_jar("example.com", f"{_HEADER}\n\n")

    def test_size_cap_enforced(self) -> None:
        """Per review adjudication: jars are capped at 1 MB."""
        big = _line("example.com", value="v" * (MAX_JAR_BYTES + 1))
        with pytest.raises(CookieJarError):
            validate_jar("example.com", big)

    @pytest.mark.parametrize(
        "domain",
        [
            "",
            ".example.com",  # declared domain must be bare (no leading dot)
            "EXAMPLE.com",  # must be lowercase
            "example",  # not a registrable domain (single label)
            "example.com/",
            "https://example.com",
            "exa mple.com",
            "../../etc/passwd",
            "example.com\tx",
        ],
    )
    def test_invalid_declared_domain_rejected(self, domain: str) -> None:
        with pytest.raises(CookieJarError):
            validate_jar(domain, _line("example.com"))

    def test_error_messages_carry_no_cookie_values(self) -> None:
        """Validation messages are self-authored (line numbers only) — cookie
        names and values never reach a response detail."""
        jar = _line("other.org", name="sid", value="super-secret-value")
        with pytest.raises(CookieJarError) as excinfo:
            validate_jar("example.com", jar)
        message = str(excinfo.value)
        assert "super-secret-value" not in message
        assert "sid" not in message
        assert "other.org" not in message


class TestStorage:
    def test_store_jar_owner_only_with_exact_content(self, tmp_path: Path) -> None:
        """Spec scenario: a well-formed jar lands at <data_dir>/cookies/
        <domain>.txt with mode 0600 and the exact jar content; dir is 0700."""
        store_jar(tmp_path, "example.com", _VALID_JAR)
        path = tmp_path / "cookies" / "example.com.txt"
        assert path.read_text() == _VALID_JAR
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700

    def test_pre_existing_loose_cookies_dir_is_rehardened(self, tmp_path: Path) -> None:
        """A cookies/ dir created earlier with loose permissions is re-hardened
        to 0700 on use — ``mkdir(mode=...)`` alone ignores pre-existing dirs,
        and jar filenames are metadata (which domains hold logins). Mirrors
        the engine-state re-hardening in settings.load_engine_state (OCR
        review on PR #12)."""
        loose = tmp_path / "cookies"
        loose.mkdir()
        # chmod, not mkdir(mode=...): the mode argument is umask-subjected, so
        # under a strict umask the dir would already be 0700 and this test
        # would pass without exercising the re-hardening path.
        loose.chmod(0o755)
        assert stat.S_IMODE(loose.stat().st_mode) == 0o755, "precondition: dir must be loose"
        store_jar(tmp_path, "example.com", _VALID_JAR)
        assert stat.S_IMODE(loose.stat().st_mode) == 0o700

    def test_store_replaces_previous_jar(self, tmp_path: Path) -> None:
        store_jar(tmp_path, "example.com", _line("example.com", value="old"))
        replacement = _line("example.com", value="new")
        store_jar(tmp_path, "example.com", replacement)
        assert jar_path(tmp_path, "example.com").read_text() == replacement

    def test_list_jars_metadata_only(self, tmp_path: Path) -> None:
        """Spec scenario: listing exposes domains and timestamps only."""
        store_jar(tmp_path, "example.com", _VALID_JAR)
        store_jar(tmp_path, "x.com", _line("x.com"))
        listed = list_jars(tmp_path)
        assert [info["domain"] for info in listed] == ["example.com", "x.com"]
        for info in listed:
            assert set(info) == {"domain", "created_at"}
            assert info["created_at"] > 0

    def test_list_jars_empty_without_dir(self, tmp_path: Path) -> None:
        assert list_jars(tmp_path) == []

    def test_delete_jar_removes_file(self, tmp_path: Path) -> None:
        """Spec scenario: delete removes the jar and the domain leaves the
        listing."""
        store_jar(tmp_path, "example.com", _VALID_JAR)
        assert delete_jar(tmp_path, "example.com") is True
        assert not jar_path(tmp_path, "example.com").exists()
        assert list_jars(tmp_path) == []

    def test_delete_absent_jar_returns_false(self, tmp_path: Path) -> None:
        assert delete_jar(tmp_path, "example.com") is False

    def test_delete_rejects_non_hostname_domains(self, tmp_path: Path) -> None:
        """Traversal-shaped domains are treated as absent, never resolved."""
        cookies_dir(tmp_path)
        (tmp_path / "victim.txt").write_text("do not delete")
        assert delete_jar(tmp_path, "../victim") is False
        assert (tmp_path / "victim.txt").read_text() == "do not delete"

    def test_storage_never_logs_jar_content(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Task 2.5 sweep: jar content appears in no log record."""
        with caplog.at_level(logging.DEBUG):
            store_jar(tmp_path, "example.com", _VALID_JAR)
            list_jars(tmp_path)
            resolve_jar(tmp_path, "example.com")
            delete_jar(tmp_path, "example.com")
        assert "secret-cookie-value" not in caplog.text


class TestResolveJar:
    def test_exact_host_matches(self, tmp_path: Path) -> None:
        store_jar(tmp_path, "x.com", _line("x.com"))
        assert resolve_jar(tmp_path, "x.com") == jar_path(tmp_path, "x.com")

    def test_subdomain_host_matches(self, tmp_path: Path) -> None:
        """Spec scenario: a `media.example.com` host matches the stored
        `example.com` jar."""
        store_jar(tmp_path, "example.com", _line("example.com"))
        assert resolve_jar(tmp_path, "media.example.com") == jar_path(tmp_path, "example.com")

    def test_label_boundary_respected(self, tmp_path: Path) -> None:
        store_jar(tmp_path, "example.com", _line("example.com"))
        assert resolve_jar(tmp_path, "notexample.com") is None

    def test_no_match_returns_none(self, tmp_path: Path) -> None:
        store_jar(tmp_path, "example.com", _line("example.com"))
        assert resolve_jar(tmp_path, "other.org") is None

    def test_most_specific_domain_wins(self, tmp_path: Path) -> None:
        store_jar(tmp_path, "example.com", _line("example.com"))
        store_jar(tmp_path, "media.example.com", _line("media.example.com"))
        assert resolve_jar(tmp_path, "media.example.com") == jar_path(tmp_path, "media.example.com")

    def test_resolve_for_url_source(self, tmp_path: Path) -> None:
        store_jar(tmp_path, "x.com", _line("x.com"))
        path = resolve_jar_for_source(tmp_path, "https://x.com/user/status/1")
        assert path == jar_path(tmp_path, "x.com")

    def test_resolve_for_source_host_is_case_insensitive(self, tmp_path: Path) -> None:
        store_jar(tmp_path, "x.com", _line("x.com"))
        assert resolve_jar_for_source(tmp_path, "https://X.COM/a") == jar_path(tmp_path, "x.com")

    def test_resolve_for_local_file_source(self, tmp_path: Path) -> None:
        store_jar(tmp_path, "x.com", _line("x.com"))
        assert resolve_jar_for_source(tmp_path, "/home/user/episode.mp3") is None
