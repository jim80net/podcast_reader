"""CSP and package-data gates for the private browser reader."""

from __future__ import annotations

import base64
import hashlib
import re

import pytest

from podcast_reader.engine.web_surface import SHELL_CSP, asset_bytes, transcript_csp
from podcast_reader.html import (
    _RAIL_SCRIPT,
    _RAIL_SCRIPT_V1,
    _SCROLL_SCRIPT,
    _SEARCH_HTML,
    _SEARCH_SCRIPT,
    _SYNC_SCRIPT,
    _SYNC_SCRIPT_V1,
    build_html,
)

_SCRIPT_RE = re.compile(r"<script>(.*?)</script>", re.DOTALL)
_SEGMENTS = [{"start": 0.0, "end": 2.0, "text": "A transcript sentence."}]
_CHAPTERS = [
    {
        "start": 0.0,
        "end": 2.0,
        "title": "Opening",
        "abstract": "The opening.",
        "type": "content",
        "key_points": [],
    }
]


def _hash(script: str) -> str:
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    return f"'sha256-{digest}'"


def _document(*scripts: str) -> bytes:
    tags = "".join(f"<script>{script}</script>" for script in scripts)
    return f'<html><body><div id="content"></div>{tags}</body></html>'.encode()


def test_shell_assets_are_packaged_and_external_script_only() -> None:
    shell = asset_bytes("shell.html").decode()
    script = asset_bytes("app.js").decode()
    stylesheet = asset_bytes("app.css").decode()
    assert '<script type="module" src="/web/assets/app.js"></script>' in shell
    assert '<link rel="stylesheet" href="/web/assets/app.css">' in shell
    assert "script-src 'self'" in SHELL_CSP
    assert "'unsafe-inline'" not in SHELL_CSP
    assert "innerHTML" not in script
    assert "serviceWorker" not in script
    assert ".library" in stylesheet


def test_artifact_csp_hashes_exact_emitted_text_for_each_renderer_path() -> None:
    documents = [
        build_html([], "Empty"),
        build_html(_SEGMENTS, "Keyless"),
        build_html(_SEGMENTS, "Chaptered", chapters=_CHAPTERS),
    ]
    for document in documents:
        scripts = _SCRIPT_RE.findall(document)
        csp = transcript_csp(document.encode())
        assert scripts
        actual_hashes = set(re.findall(r"'sha256-[A-Za-z0-9+/=]+'", csp))
        assert actual_hashes == {_hash(script) for script in scripts}

    # Hash the exact text node including build_html's leading newline, not the
    # bare constant. These digests must differ and only the emitted one passes.
    empty_csp = transcript_csp(documents[0].encode())
    assert _hash(f"\n{_SYNC_SCRIPT}") in empty_csp
    assert _hash(_SYNC_SCRIPT) not in empty_csp


def test_artifact_csp_preserves_exact_legacy_shapes() -> None:
    scroll = f"\n{_SCROLL_SCRIPT}"
    rail = f"\n{_RAIL_SCRIPT_V1}"
    sync = f"\n{_SYNC_SCRIPT_V1}"
    for sequence in [(), (scroll,), (rail,), (sync,), (scroll, sync), (rail, sync)]:
        csp = transcript_csp(_document(*sequence))
        actual = set(re.findall(r"'sha256-[A-Za-z0-9+/=]+'", csp))
        assert actual == {_hash(script) for script in sequence}


def test_legacy_sync_script_digest_is_byte_stable() -> None:
    assert hashlib.sha256(_SYNC_SCRIPT_V1.encode()).hexdigest() == (
        "a2dc9e5d8b1cb318cebc2c7b7d861d91db59612b600a2f3aed05285f359e6102"
    )


def test_current_sync_script_uses_the_search_capacity_bounds_before_array_work() -> None:
    assert "querySelectorAll('p[data-start]')" not in _SYNC_SCRIPT
    assert "document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT)" in _SYNC_SCRIPT
    assert "if (visits > 100000) return;" in _SYNC_SCRIPT
    assert "if (nodes.length > 10000) return;" in _SYNC_SCRIPT


@pytest.mark.parametrize(
    "document",
    [
        _document(f"\n{_SYNC_SCRIPT}", f"\n{_SEARCH_SCRIPT}", f"\n{_SEARCH_SCRIPT}"),
        _document(f"\n{_SEARCH_SCRIPT}", f"\n{_SYNC_SCRIPT}"),
        _document(f"\n{_RAIL_SCRIPT}", f"\n{_SEARCH_SCRIPT}", f"\n{_SYNC_SCRIPT}"),
        _document(f"\n{_SYNC_SCRIPT}", "alert('unknown')", f"\n{_SEARCH_SCRIPT}"),
        (
            f"<html><head><script>\n{_SYNC_SCRIPT}</script></head><body>"
            f'<div id="content"></div><script>\n{_SEARCH_SCRIPT}</script></body></html>'
        ).encode(),
    ],
)
def test_known_scripts_in_noncanonical_shape_are_never_blessed(document: bytes) -> None:
    assert "script-src 'none'" in transcript_csp(document)


def test_new_script_tuples_are_bound_to_exact_renderer_shape_and_search_controls() -> None:
    empty = build_html([], "Empty")
    rail = build_html(_SEGMENTS, "Keyless")
    sidebar = build_html(_SEGMENTS, "Chaptered", chapters=_CHAPTERS)

    wrong_shapes = [
        empty.replace("<main>", '<nav class="timeline-nav"><main>'),
        rail.replace('class="timeline-nav"', 'class="timeline-nav-broken"'),
        sidebar.replace('<aside id="sidebar">', '<aside id="sidebar-broken">'),
    ]
    broken_controls = [
        rail.replace('class="transcript-search-input"', 'class="transcript-search-input-broken"'),
        rail.replace(
            '<button type="button" class="transcript-search-next"',
            '<button type="button" class="transcript-search-next" disabled></button>'
            '<button type="button" class="transcript-search-next"',
        ),
        rail.replace('class="transcript-search" role="search"', 'class="transcript-search"'),
        rail.replace(
            '<div class="transcript-search-row">',
            '<div class="transcript-search-row"><input type="text">',
        ),
        rail.replace(_SEARCH_HTML, "").replace("<main>", f"<main>{_SEARCH_HTML}"),
    ]
    for document in [*wrong_shapes, *broken_controls]:
        assert "script-src 'none'" in transcript_csp(document.encode())


def test_unknown_injected_script_is_never_blessed_by_csp() -> None:
    injected = b"<html><script>alert('not renderer code')</script></html>"
    csp = transcript_csp(injected)
    assert "script-src 'none'" in csp
    assert _hash("alert('not renderer code')") not in csp


def test_artifact_csp_permits_no_remote_font_provider() -> None:
    csp = transcript_csp(build_html(_SEGMENTS, "Private transcript").encode())
    assert "fonts.googleapis.com" not in csp
    assert "fonts.gstatic.com" not in csp
    assert "style-src 'unsafe-inline';" in csp
    assert "font-src 'none';" in csp
