"""Cookie-jar storage: Netscape-format validation + owner-only persisted files.

Jars are real credentials at rest — the deliberate divergence from the
never-persisted key store, because yt-dlp takes ``--cookies <FILE>``. The
compensating discipline (cookie-management spec): every jar is validated
before storing (Netscape parse, domain suffix-match with leading dots
stripped per U4, 1 MB cap), written atomically with mode 0600 into a 0700
``<data_dir>/cookies/`` dir, listed as metadata only (domain + created_at),
and its content appears in no API response, no log, and no diagnostic
output. Validation messages reference line numbers — never cookie names,
values, or jar-derived strings — so the API layer may echo them as 400
details.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

# pydantic (the GET /v1/cookies response model) requires
# typing_extensions.TypedDict on Python < 3.12 (same note as types.py).
from typing_extensions import TypedDict

from podcast_reader.engine.settings import atomic_write_text

if TYPE_CHECKING:
    from pathlib import Path

COOKIES_DIR = "cookies"
#: 1 MB cap (per review adjudication): SSO-heavy domains can legitimately
#: carry dozens of cookies at up to ~4 KB apiece plus jar overhead, so
#: 256 KB could clip a real jar while 1 MB still bounds abuse.
MAX_JAR_BYTES = 1024 * 1024

_NETSCAPE_FIELDS = 7
_HTTPONLY_PREFIX = "#HttpOnly_"

#: Bare lowercase hostname with at least two labels (a registrable domain,
#: per U4). Also the storage-filename guard: no separators, dots only between
#: non-empty labels, so a declared domain can never traverse paths.
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)


class CookieJarInfo(TypedDict):
    """One ``GET /v1/cookies`` entry: metadata only — never cookie values."""

    domain: str
    created_at: float


class CookieJarError(ValueError):
    """Jar validation failure.

    The message is self-authored (declared domain and line numbers only,
    never cookie names/values), so the API layer may echo it as a 400 detail.
    """


def validate_jar(domain: str, jar: str) -> None:
    """Validate a declared domain + Netscape jar; raise :class:`CookieJarError`.

    *domain* must be a bare lowercase registrable domain. *jar* must stay
    under the 1 MB cap, parse as Netscape cookie lines (including
    ``#HttpOnly_``-prefixed entries), contain at least one cookie, and every
    cookie's domain field — with any leading ``.`` stripped first (per U4) —
    must suffix-match the declared domain.
    """
    if not _is_valid_domain(domain):
        raise CookieJarError("domain must be a bare lowercase hostname (e.g. example.com)")
    if len(jar.encode()) > MAX_JAR_BYTES:
        raise CookieJarError("cookie jar exceeds the 1 MB size cap")
    cookie_lines = 0
    for lineno, line in enumerate(jar.splitlines(), start=1):
        if line.startswith(_HTTPONLY_PREFIX):
            cookie_line = line.removeprefix(_HTTPONLY_PREFIX)
        elif not line.strip() or line.startswith("#"):
            continue  # header, comments, blank lines
        else:
            cookie_line = line
        fields = cookie_line.split("\t")
        if len(fields) != _NETSCAPE_FIELDS:
            raise CookieJarError(
                f"line {lineno}: not a Netscape cookie line "
                f"(expected {_NETSCAPE_FIELDS} tab-separated fields)"
            )
        cookie_domain = fields[0].lower().removeprefix(".")  # leading-dot strip (per U4)
        if cookie_domain != domain and not cookie_domain.endswith("." + domain):
            raise CookieJarError(
                f"line {lineno}: cookie domain does not match the declared domain {domain!r}"
            )
        cookie_lines += 1
    if cookie_lines == 0:
        raise CookieJarError("cookie jar contains no cookie lines")


def cookies_dir(base: Path) -> Path:
    """The jar directory ``<data_dir>/cookies``, created owner-only (0700)."""
    directory = base / COOKIES_DIR
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    return directory


def jar_path(base: Path, domain: str) -> Path:
    """Storage path for *domain*'s jar (``<data_dir>/cookies/<domain>.txt``)."""
    return cookies_dir(base) / f"{domain}.txt"


def store_jar(base: Path, domain: str, jar: str) -> None:
    """Persist a validated jar atomically with mode 0600, replacing any prior.

    Callers validate first (:func:`validate_jar`); storing re-checks the
    domain shape as the filename guard of last resort.
    """
    if not _is_valid_domain(domain):
        raise CookieJarError("domain must be a bare lowercase hostname (e.g. example.com)")
    atomic_write_text(jar_path(base, domain), jar, mode=0o600)


def list_jars(base: Path) -> list[CookieJarInfo]:
    """Stored-jar metadata, sorted by domain — never jar content."""
    directory = base / COOKIES_DIR
    if not directory.is_dir():
        return []
    return [
        CookieJarInfo(domain=path.stem, created_at=path.stat().st_mtime)
        for path in sorted(directory.glob("*.txt"))
    ]


def delete_jar(base: Path, domain: str) -> bool:
    """Remove *domain*'s jar; False when absent (or not a valid domain name)."""
    if not _is_valid_domain(domain):
        return False  # traversal-shaped names can never address a stored jar
    path = base / COOKIES_DIR / f"{domain}.txt"
    if not path.is_file():
        return False
    path.unlink()
    return True


def resolve_jar(base: Path, host: str) -> Path | None:
    """The most specific stored jar whose domain suffix-matches *host*.

    Match rule (cookie-management spec): host equals the domain, or ends with
    ``.`` + the domain. ``None`` when no jar matches — the caller falls back
    to the ``YT_DLP_COOKIES`` environment variable.
    """
    host = host.lower()
    best: str | None = None
    for info in list_jars(base):
        domain = info["domain"]
        if host != domain and not host.endswith("." + domain):
            continue
        if best is None or len(domain) > len(best):
            best = domain
    return None if best is None else jar_path(base, best)


def resolve_jar_for_source(base: Path, source: str) -> Path | None:
    """Resolve a job source (URL or local path) to a stored jar, if any."""
    if not source.startswith(("http://", "https://")):
        return None
    host = urlsplit(source).hostname
    if not host:
        return None
    return resolve_jar(base, host)


def _is_valid_domain(domain: str) -> bool:
    return _DOMAIN_RE.fullmatch(domain) is not None
