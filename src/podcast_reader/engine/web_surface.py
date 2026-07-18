"""Static shell assets and CSP construction for the private web reader."""

from __future__ import annotations

import base64
import hashlib
from html.parser import HTMLParser
from importlib.resources import files

from podcast_reader.engine.script_policy import ScriptPin, compile_script_policy
from podcast_reader.html import (
    _EXPORT_SCRIPT,
    _RAIL_SCRIPT,
    _RAIL_SCRIPT_V1,
    _SCROLL_SCRIPT,
    _SEARCH_SCRIPT,
    _SEARCH_SCRIPT_V1,
    _SYNC_SCRIPT,
    _SYNC_SCRIPT_V1,
)

SHELL_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; "
    "connect-src 'self'; frame-src 'self'; img-src 'self' data:; "
    "base-uri 'none'; form-action 'self'; frame-ancestors 'none'; object-src 'none'"
)


def _text(script: str) -> str:
    return f"\n{script}"


_SCROLL = _text(_SCROLL_SCRIPT)
_RAIL_V1 = _text(_RAIL_SCRIPT_V1)
_RAIL_V2 = _text(_RAIL_SCRIPT)
_SYNC_V1 = _text(_SYNC_SCRIPT_V1)
_SYNC_V2 = _text(_SYNC_SCRIPT)
_SEARCH_V1 = _text(_SEARCH_SCRIPT_V1)
_SEARCH_V2 = _text(_SEARCH_SCRIPT)
_EXPORT_V1 = _text(_EXPORT_SCRIPT)

_TRANSCRIPT_SCRIPT_PINS = (
    ScriptPin(
        "scroll-v1", _SCROLL, "4918667b859797821d76ba6b013c4e7302955a8e3a97bf35f9808804cf218edb"
    ),
    ScriptPin(
        "rail-v1", _RAIL_V1, "cf52a37bfb57285a94f6bf25e65b399e0a2f826dfa224b817c70f993b3cadc80"
    ),
    ScriptPin(
        "rail-v2", _RAIL_V2, "51a9806b953ed0866c0213bbad4efd52b42eb52feb1704975a9508d26ab27a46"
    ),
    ScriptPin(
        "sync-v1", _SYNC_V1, "3681c1372593523cbceba3ffaacc9ccf3adfc67279da8bcd89ad102257296d97"
    ),
    ScriptPin(
        "sync-v2", _SYNC_V2, "36da7e8aa7c5f869f042ad7dd4a49034de6487c653e9ce68cc81da48c552b9ba"
    ),
    ScriptPin(
        "search-v1", _SEARCH_V1, "63b437ad235772db86afd9932809a3697edc7adb9e93d3e1708033b1db822d82"
    ),
    ScriptPin(
        "search-v2", _SEARCH_V2, "c77ddac5429f5d047b1f08b3430f787536832e6f11f2f9d223216e98c83988c6"
    ),
    ScriptPin(
        "export-v1", _EXPORT_V1, "acd2b0de74c66942282339dfd5fc86b64fb21d4594c06179a495dd8b81b6c2dc"
    ),
)

# Compatibility starts at the first private-web CSP release (#83). At that
# boundary the renderer emitted the V1 rail/sync texts below; singleton shapes
# remain accepted for older empty/pre-combination artifacts. Search V1 remains
# authorized only in its exact just-shipped tuples from #90. Arbitrary subsets,
# orders, and mixes are never blessed.
_TRANSCRIPT_SCRIPT_SEQUENCE_NAMES = (
    (),
    ("scroll-v1",),
    ("rail-v1",),
    ("sync-v1",),
    ("scroll-v1", "sync-v1"),
    ("rail-v1", "sync-v1"),
    ("sync-v2", "search-v1"),
    ("rail-v2", "sync-v2", "search-v1"),
    ("scroll-v1", "sync-v2", "search-v1"),
    ("sync-v2", "search-v2"),
    ("rail-v2", "sync-v2", "search-v2"),
    ("scroll-v1", "sync-v2", "search-v2"),
    ("sync-v2", "search-v2", "export-v1"),
    ("rail-v2", "sync-v2", "search-v2", "export-v1"),
    ("scroll-v1", "sync-v2", "search-v2", "export-v1"),
)
_TRANSCRIPT_SCRIPT_POLICY = compile_script_policy(
    _TRANSCRIPT_SCRIPT_PINS, _TRANSCRIPT_SCRIPT_SEQUENCE_NAMES
)
# A bad or stale pin makes this empty: transcript_csp then emits script-src
# 'none' rather than silently blessing edited text.
_ALLOWED_SCRIPT_SEQUENCES = _TRANSCRIPT_SCRIPT_POLICY.sequences
_EXPECTED_SCRIPT_SHAPES = {
    (_SYNC_V2, _SEARCH_V1): ("empty", True),
    (_RAIL_V2, _SYNC_V2, _SEARCH_V1): ("rail", True),
    (_SCROLL, _SYNC_V2, _SEARCH_V1): ("sidebar", True),
    (_SYNC_V2, _SEARCH_V2): ("empty", True),
    (_RAIL_V2, _SYNC_V2, _SEARCH_V2): ("rail", True),
    (_SCROLL, _SYNC_V2, _SEARCH_V2): ("sidebar", True),
    (_SYNC_V2, _SEARCH_V2, _EXPORT_V1): ("empty", True),
    (_RAIL_V2, _SYNC_V2, _SEARCH_V2, _EXPORT_V1): ("rail", True),
    (_SCROLL, _SYNC_V2, _SEARCH_V2, _EXPORT_V1): ("sidebar", True),
}
_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
_SEARCH_ELEMENT_SHAPES: dict[str, tuple[str, dict[str, str | None]]] = {
    "search": ("div", {"class": "transcript-search", "role": "search"}),
    "search-panel": (
        "div",
        {"id": "transcript-search-panel", "class": "transcript-search-panel", "hidden": None},
    ),
    "search-row": ("div", {"class": "transcript-search-row"}),
    "transcript-search-toggle": (
        "button",
        {
            "type": "button",
            "class": "transcript-search-toggle",
            "aria-controls": "transcript-search-panel",
            "aria-expanded": "false",
            "aria-keyshortcuts": "/",
        },
    ),
    "transcript-search-label": (
        "label",
        {"class": "transcript-search-label", "for": "transcript-search-input"},
    ),
    "transcript-search-input": (
        "input",
        {
            "id": "transcript-search-input",
            "class": "transcript-search-input",
            "type": "search",
            "autocomplete": "off",
            "spellcheck": "false",
            "autocorrect": "off",
            "autocapitalize": "none",
            "inputmode": "search",
            "placeholder": "Find in transcript",
        },
    ),
    "transcript-search-prev": (
        "button",
        {
            "type": "button",
            "class": "transcript-search-prev",
            "aria-label": "Previous match",
            "disabled": None,
        },
    ),
    "transcript-search-next": (
        "button",
        {
            "type": "button",
            "class": "transcript-search-next",
            "aria-label": "Next match",
            "disabled": None,
        },
    ),
    "transcript-search-read": (
        "button",
        {
            "type": "button",
            "class": "transcript-search-read",
            "aria-label": "Read current passage",
            "disabled": None,
        },
    ),
    "transcript-search-clear": (
        "button",
        {
            "type": "button",
            "class": "transcript-search-clear",
            "aria-label": "Clear and close search",
        },
    ),
    "transcript-search-status": (
        "p",
        {"class": "transcript-search-status", "role": "status", "aria-live": "polite"},
    ),
}


class _ScriptShapeParser(HTMLParser):
    """Capture only canonical direct-body renderer scripts after ``#content``."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.stack: list[tuple[str, bool, str]] = []
        self.scripts: list[str] = []
        self.script_parts: list[str] | None = None
        self.content_seen = 0
        self.content_closed = False
        self.invalid = False
        self.body_has_sidebar = False
        self.sidebar_count = 0
        self.timeline_count = 0
        self.search_parts: dict[str, int] = {}
        self.content_children: list[str] = []

    def _count_search_part(self, name: str) -> None:
        self.search_parts[name] = self.search_parts.get(name, 0) + 1

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = {name.lower(): value for name, value in attrs}
        parent = self.stack[-1][0] if self.stack else ""
        parent_marker = self.stack[-1][2] if self.stack else ""
        is_content = tag == "div" and attributes.get("id") == "content"
        marker = ""
        if tag == "body":
            classes = (attributes.get("class") or "").split()
            self.body_has_sidebar = classes == ["has-sidebar"]
        elif is_content:
            marker = "content"
        elif tag == "aside" and attributes.get("id") == "sidebar" and parent == "body":
            marker = "sidebar"
            self.sidebar_count += 1
        elif tag == "main" and parent_marker == "content":
            marker = "main"
        elif tag == "header" and parent_marker == "content":
            marker = "header"
        elif tag == "footer" and parent_marker == "content":
            marker = "footer"
        elif tag == "nav" and attributes.get("class") == "timeline-nav" and parent_marker == "main":
            marker = "timeline"
            self.timeline_count += 1
        elif (
            tag == "div"
            and attributes.get("class") == "transcript-search"
            and attributes.get("role") == "search"
            and parent_marker == "content"
        ):
            marker = "search"
            self._count_search_part("root")
        elif (
            tag == "div"
            and attributes.get("class") == "transcript-search-panel"
            and attributes.get("id") == "transcript-search-panel"
            and parent_marker == "search"
        ):
            marker = "search-panel"
            self._count_search_part("panel")
        elif (
            tag == "div"
            and attributes.get("class") == "transcript-search-row"
            and parent_marker == "search-panel"
        ):
            marker = "search-row"
            self._count_search_part("row")
        elif parent_marker in {"search", "search-row", "search-panel"}:
            search_class = attributes.get("class") or ""
            expected_parent = {
                "transcript-search-toggle": "search",
                "transcript-search-label": "search-panel",
                "transcript-search-input": "search-row",
                "transcript-search-prev": "search-row",
                "transcript-search-next": "search-row",
                "transcript-search-read": "search-row",
                "transcript-search-clear": "search-row",
                "transcript-search-status": "search-panel",
            }.get(search_class)
            if expected_parent == parent_marker:
                marker = search_class
                self._count_search_part(search_class)
        if parent_marker == "content":
            if marker not in {"header", "search", "main", "footer"}:
                self.invalid = True
            else:
                self.content_children.append(marker)
        if parent_marker in {"search", "search-panel", "search-row"} and not marker:
            self.invalid = True
        if marker in _SEARCH_ELEMENT_SHAPES:
            expected_tag, expected_attributes = _SEARCH_ELEMENT_SHAPES[marker]
            if (
                tag != expected_tag
                or attributes != expected_attributes
                or len(attrs) != len(attributes)
            ):
                self.invalid = True
        if is_content:
            self.content_seen += 1
            if parent != "body" or self.content_closed:
                self.invalid = True
        if tag == "script":
            if (
                attrs
                or parent != "body"
                or not self.content_closed
                or self.script_parts is not None
            ):
                self.invalid = True
            self.script_parts = []
        elif self.content_closed:
            self.invalid = True
        if tag not in _VOID_TAGS:
            self.stack.append((tag, is_content, marker))

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _VOID_TAGS:
            return
        if not self.stack or self.stack[-1][0] != tag:
            self.invalid = True
            return
        closed_tag, is_content, _marker = self.stack.pop()
        if closed_tag == "script":
            if self.script_parts is None:
                self.invalid = True
            else:
                self.scripts.append("".join(self.script_parts))
                self.script_parts = None
        elif is_content:
            self.content_closed = True

    def handle_data(self, data: str) -> None:
        if self.script_parts is not None:
            self.script_parts.append(data)
        elif self.content_closed and data.strip():
            self.invalid = True

    def handle_comment(self, data: str) -> None:
        if self.content_closed:
            self.invalid = True

    def sequence(self) -> tuple[str, ...] | None:
        if (
            self.invalid
            or self.stack
            or self.script_parts is not None
            or self.content_seen != 1
            or not self.content_closed
        ):
            return None
        return tuple(self.scripts)

    def artifact_shape(self) -> tuple[str, bool] | None:
        if self.sidebar_count:
            if self.sidebar_count != 1 or not self.body_has_sidebar or self.timeline_count:
                return None
            shape = "sidebar"
        elif self.timeline_count:
            if self.timeline_count != 1 or self.body_has_sidebar:
                return None
            shape = "rail"
        elif self.body_has_sidebar:
            return None
        else:
            shape = "empty"
        has_search = bool(self.search_parts)
        if has_search:
            required = {
                "root",
                "panel",
                "row",
                "transcript-search-toggle",
                "transcript-search-label",
                "transcript-search-input",
                "transcript-search-prev",
                "transcript-search-next",
                "transcript-search-read",
                "transcript-search-clear",
                "transcript-search-status",
            }
            if set(self.search_parts) != required or any(
                count != 1 for count in self.search_parts.values()
            ):
                return None
            if self.content_children != ["header", "search", "main", "footer"]:
                return None
        elif self.content_children and self.content_children.count("main") != 1:
            return None
        return shape, has_search


def asset_bytes(name: str) -> bytes:
    """Read a packaged shell asset without depending on the current directory."""
    return files("podcast_reader.web_assets").joinpath(name).read_bytes()


def transcript_csp(document: bytes) -> str:
    """Build CSP hashes for only the known scripts actually emitted in *document*."""
    try:
        text = document.decode("utf-8")
    except UnicodeDecodeError:
        text = ""
    parser = _ScriptShapeParser()
    try:
        parser.feed(text)
        parser.close()
        sequence = parser.sequence()
    except Exception:
        sequence = None
    hashes = []
    expected_shape = _EXPECTED_SCRIPT_SHAPES.get(sequence or ())
    shape_matches = expected_shape is None or parser.artifact_shape() == expected_shape
    if sequence in _ALLOWED_SCRIPT_SEQUENCES and shape_matches:
        for script in sequence or ():
            digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
            hashes.append(f"'sha256-{digest}'")
    script_src = " ".join(dict.fromkeys(hashes)) or "'none'"
    return (
        f"default-src 'none'; script-src {script_src}; "
        "style-src 'unsafe-inline'; font-src 'none'; img-src data:; "
        "connect-src 'none'; media-src 'none'; frame-src 'none'; "
        "object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'"
    )
