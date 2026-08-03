from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[3]
PUBLIC_COPY = (
    ROOT / "README.md",
    ROOT / "services" / "premium" / "README.md",
    *sorted(
        (ROOT / "services" / "premium" / "src" / "podcast_reader_premium" / "templates").glob(
            "*.html"
        )
    ),
    *sorted((ROOT / "apps" / "desktop" / "renderer" / "src").rglob("*.ts")),
    *sorted((ROOT / "apps" / "desktop" / "renderer" / "src").rglob("*.tsx")),
)

CONTRADICTIONS = (
    re.compile(
        r"premium (?:service|api).{0,80}(?:has no|never (?:receives|gets)).{0,40}transcript",
        re.I | re.S,
    ),
    re.compile(r"transcript text never reaches (?:our|the) (?:service|server)", re.I),
    re.compile(
        r"(?:transcript|title).{0,80}(?:never reaches|does not reach).{0,40}(?:service|server)",
        re.I | re.S,
    ),
)


def test_public_copy_cannot_restore_an_unconditional_local_only_claim() -> None:
    violations: list[str] = []
    for path in PUBLIC_COPY:
        if not path.is_file():
            continue
        source = path.read_text(encoding="utf-8")
        if any(pattern.search(source) for pattern in CONTRADICTIONS):
            violations.append(str(path.relative_to(ROOT)))
    assert violations == []


def test_premium_copy_discloses_the_approved_content_stateless_boundary() -> None:
    source = (ROOT / "services" / "premium" / "README.md").read_text(encoding="utf-8")
    required = (
        "explicitly requests transcript email",
        "one bounded plain-text transcript",
        "keeps no transcript copy",
        "application memory",
        "DEV Maildir destination, which retains",
        "Audio, feed URLs",
        "no SMTP or provider configuration",
    )
    assert all(statement in source for statement in required)


def test_copy_fence_has_a_negative_proof() -> None:
    contradiction = "The premium service has no transcript fields."
    assert any(pattern.search(contradiction) for pattern in CONTRADICTIONS)
