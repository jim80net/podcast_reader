"""Static shell assets and CSP construction for the private web reader."""

from __future__ import annotations

import base64
import hashlib
import re
from importlib.resources import files

from podcast_reader.html import _RAIL_SCRIPT, _SCROLL_SCRIPT, _SYNC_SCRIPT

SHELL_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; "
    "connect-src 'self'; frame-src 'self'; img-src 'self' data:; "
    "base-uri 'none'; form-action 'self'; frame-ancestors 'none'; object-src 'none'"
)

_SCRIPT_RE = re.compile(r"<script>(.*?)</script>", re.DOTALL)
_ALLOWED_SCRIPT_TEXT = frozenset(
    {f"\n{script}" for script in (_SCROLL_SCRIPT, _RAIL_SCRIPT, _SYNC_SCRIPT)}
)


def asset_bytes(name: str) -> bytes:
    """Read a packaged shell asset without depending on the current directory."""
    return files("podcast_reader.web_assets").joinpath(name).read_bytes()


def transcript_csp(document: bytes) -> str:
    """Build CSP hashes for only the known scripts actually emitted in *document*."""
    try:
        text = document.decode("utf-8")
    except UnicodeDecodeError:
        text = ""
    hashes = []
    for script in _SCRIPT_RE.findall(text):
        if script not in _ALLOWED_SCRIPT_TEXT:
            continue
        digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
        hashes.append(f"'sha256-{digest}'")
    script_src = " ".join(dict.fromkeys(hashes)) or "'none'"
    return (
        f"default-src 'none'; script-src {script_src}; "
        "style-src 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; img-src data:; "
        "connect-src 'none'; media-src 'none'; frame-src 'none'; "
        "object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
    )
