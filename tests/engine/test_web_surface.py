"""CSP and package-data gates for the private browser reader."""

from __future__ import annotations

import base64
import hashlib
import re

from podcast_reader.engine.web_surface import SHELL_CSP, asset_bytes, transcript_csp
from podcast_reader.html import _SYNC_SCRIPT, build_html

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


def test_unknown_injected_script_is_never_blessed_by_csp() -> None:
    injected = b"<html><script>alert('not renderer code')</script></html>"
    csp = transcript_csp(injected)
    assert "script-src 'none'" in csp
    assert _hash("alert('not renderer code')") not in csp
