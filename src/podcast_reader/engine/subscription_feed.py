"""SSRF-safe feed retrieval and bounded RSS/Atom parsing."""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import socket
import ssl
import time
import zlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Protocol, cast
from urllib.parse import urljoin, urlsplit, urlunsplit

from defusedxml import ElementTree

if TYPE_CHECKING:
    from xml.etree.ElementTree import Element

MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_COMPRESSION_RATIO = 40
MAX_REDIRECTS = 5
MAX_ENTRIES = 500
MAX_TEXT = 4096
ALLOWED_PORTS = frozenset({443})
ALLOWED_CONTENT_TYPES = frozenset(
    {
        "application/atom+xml",
        "application/rss+xml",
        "application/xml",
        "text/xml",
    }
)


class FeedError(ValueError):
    """A bounded, URL-free feed failure safe to persist or return."""


class FeedTemporaryError(FeedError):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("feed temporarily unavailable")
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class FeedEpisode:
    episode_key: str
    guid: str | None
    enclosure_url: str
    title: str | None
    published_at: str | None


@dataclass(frozen=True)
class ParsedFeed:
    title: str | None
    episodes: tuple[FeedEpisode, ...]


@dataclass(frozen=True)
class FeedResponse:
    status: int
    final_url: str
    content: bytes
    etag: str | None
    last_modified: str | None
    retry_after_seconds: int | None = None


class FeedFetcher(Protocol):
    def fetch(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        should_continue: Callable[[], bool] = lambda: True,
    ) -> FeedResponse: ...


Resolver = Callable[[str, int], Sequence[str]]


def _default_resolver(host: str, port: int) -> Sequence[str]:
    return tuple(
        sorted(
            {
                cast("str", item[4][0])
                for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            }
        )
    )


class CachingResolver:
    """Bound DNS work per parsed feed while retaining full address checks."""

    def __init__(self, resolver: Resolver = _default_resolver, *, max_hosts: int = 32) -> None:
        self._resolver = resolver
        self._max_hosts = max_hosts
        self._cache: dict[tuple[str, int], tuple[str, ...]] = {}

    def __call__(self, host: str, port: int) -> Sequence[str]:
        key = (host, port)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        if len(self._cache) >= self._max_hosts:
            raise FeedError("feed referenced too many distinct media hosts")
        addresses = tuple(self._resolver(host, port))
        self._cache[key] = addresses
        return addresses


def _is_public_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return bool(address.is_global and not address.is_multicast and not address.is_reserved)


def validate_feed_url(url: str, *, resolver: Resolver = _default_resolver) -> tuple[str, str]:
    """Return canonical URL and normalized origin after DNS/IP safety checks."""
    try:
        parts = urlsplit(url)
        port = parts.port or 443
    except ValueError as exc:
        raise FeedError("invalid feed URL") from exc
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
        or port not in ALLOWED_PORTS
    ):
        raise FeedError("feed URL must be absolute HTTPS on an allowed port")
    host = parts.hostname.rstrip(".").lower()
    if not host:
        raise FeedError("invalid feed host")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    addresses = (str(literal),) if literal is not None else tuple(resolver(host, port))
    if not addresses or any(not _is_public_address(address) for address in addresses):
        raise FeedError("feed host did not resolve exclusively to public addresses")
    netloc = host if port == 443 else f"{host}:{port}"
    path = parts.path or "/"
    canonical = urlunsplit(("https", netloc, path, parts.query, ""))
    return canonical, f"https://{netloc}"


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """TLS connection pinned to a prevalidated DNS answer with normal SNI."""

    def __init__(
        self,
        host: str,
        port: int,
        address: str,
        *,
        timeout: float,
        context: ssl.SSLContext,
    ) -> None:
        super().__init__(host, port=port, timeout=timeout, context=context)
        self._address = address
        self._tls_context = context

    def connect(self) -> None:
        raw = socket.create_connection((self._address, self.port), self.timeout)
        try:
            self.sock = self._tls_context.wrap_socket(raw, server_hostname=self.host)
            peer = self.sock.getpeername()[0]
            if ipaddress.ip_address(peer) != ipaddress.ip_address(self._address):
                raise FeedError("feed peer did not match the validated address")
        except Exception:
            raw.close()
            raise


class SafeFeedFetcher:
    """Manual redirecting HTTPS client with no ambient proxy/cookie state."""

    def __init__(
        self,
        *,
        resolver: Resolver = _default_resolver,
        timeout_seconds: float = 10.0,
        tls_context: ssl.SSLContext | None = None,
        connection_factory: Callable[..., http.client.HTTPSConnection] = _PinnedHTTPSConnection,
    ) -> None:
        self._resolver = resolver
        self._timeout = timeout_seconds
        self._context = tls_context or ssl.create_default_context()
        self._connection_factory = connection_factory

    def fetch(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
        should_continue: Callable[[], bool] = lambda: True,
    ) -> FeedResponse:
        current = url
        deadline = time.monotonic() + self._timeout
        for redirects in range(MAX_REDIRECTS + 1):
            if not should_continue():
                raise FeedError("feed request cancelled")
            if time.monotonic() >= deadline:
                raise FeedError("feed request timed out")
            canonical, _origin = validate_feed_url(current, resolver=self._resolver)
            parts = urlsplit(canonical)
            host = parts.hostname or ""
            port = parts.port or 443
            addresses = tuple(self._resolver(host, port))
            if not addresses or any(not _is_public_address(item) for item in addresses):
                raise FeedError("feed host did not resolve exclusively to public addresses")
            connection = self._connection_factory(
                host,
                port,
                addresses[0],
                timeout=self._timeout,
                context=self._context,
            )
            headers = {
                "Accept": "application/atom+xml, application/rss+xml, application/xml, text/xml",
                "Accept-Encoding": "gzip, deflate",
                "Cache-Control": "no-cache",
                "User-Agent": "PodcastReader/0.3 feed-poller",
            }
            if etag:
                headers["If-None-Match"] = etag
            if last_modified:
                headers["If-Modified-Since"] = last_modified
            target = urlunsplit(("", "", parts.path or "/", parts.query, ""))
            try:
                connection.request("GET", target, headers=headers)
                _set_remaining_timeout(connection, deadline)
                response = connection.getresponse()
                if not should_continue():
                    raise FeedError("feed request cancelled")
                status = response.status
                if status in {301, 302, 303, 307, 308}:
                    location = response.getheader("Location")
                    _bounded_read(
                        response,
                        connection=connection,
                        deadline=deadline,
                        should_continue=should_continue,
                    )
                    if location is None or redirects == MAX_REDIRECTS:
                        raise FeedError("feed redirect limit exceeded")
                    current = urljoin(canonical, location)
                    continue
                if status == 304:
                    _bounded_read(
                        response,
                        connection=connection,
                        deadline=deadline,
                        should_continue=should_continue,
                    )
                    return FeedResponse(
                        status=304,
                        final_url=canonical,
                        content=b"",
                        etag=response.getheader("ETag") or etag,
                        last_modified=response.getheader("Last-Modified") or last_modified,
                    )
                if status == 429 or status == 503:
                    retry_after = _retry_after_seconds(response.getheader("Retry-After"))
                    _bounded_read(
                        response,
                        connection=connection,
                        deadline=deadline,
                        should_continue=should_continue,
                    )
                    raise FeedTemporaryError(retry_after)
                if status < 200 or status >= 300:
                    _bounded_read(
                        response,
                        connection=connection,
                        deadline=deadline,
                        should_continue=should_continue,
                    )
                    raise FeedError(f"feed returned HTTP {status}")
                media_type = (response.getheader("Content-Type") or "").partition(";")[0].lower()
                if media_type not in ALLOWED_CONTENT_TYPES and not media_type.endswith("+xml"):
                    raise FeedError("feed response was not an allowed XML content type")
                compressed = _bounded_read(
                    response,
                    connection=connection,
                    deadline=deadline,
                    should_continue=should_continue,
                )
                content = _decompress(compressed, response.getheader("Content-Encoding"))
                return FeedResponse(
                    status=status,
                    final_url=canonical,
                    content=content,
                    etag=response.getheader("ETag"),
                    last_modified=response.getheader("Last-Modified"),
                )
            except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
                raise FeedError("feed network request failed") from exc
            finally:
                connection.close()
        raise FeedError("feed redirect limit exceeded")


def _bounded_read(
    response: http.client.HTTPResponse,
    *,
    connection: http.client.HTTPSConnection,
    deadline: float,
    should_continue: Callable[[], bool],
) -> bytes:
    declared = response.getheader("Content-Length")
    if declared is not None:
        try:
            if int(declared) > MAX_BODY_BYTES:
                raise FeedError("feed response exceeded the size limit")
        except ValueError as exc:
            raise FeedError("feed response had an invalid content length") from exc
    chunks: list[bytes] = []
    total = 0
    while True:
        if not should_continue():
            raise FeedError("feed request cancelled")
        _set_remaining_timeout(connection, deadline)
        chunk = response.read(min(64 * 1024, MAX_BODY_BYTES + 1 - total))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if total > MAX_BODY_BYTES:
            raise FeedError("feed response exceeded the size limit")


def _set_remaining_timeout(connection: http.client.HTTPSConnection, deadline: float) -> None:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise FeedError("feed request timed out")
    sock = getattr(connection, "sock", None)
    if sock is not None:
        sock.settimeout(remaining)


def _decompress(content: bytes, encoding: str | None) -> bytes:
    normalized = (encoding or "identity").strip().lower()
    try:
        if normalized in {"", "identity"}:
            result = content
        elif normalized == "gzip":
            result = _bounded_zlib(content, zlib.MAX_WBITS | 16)
        elif normalized == "deflate":
            result = _bounded_zlib(content, zlib.MAX_WBITS)
        else:
            raise FeedError("feed used an unsupported content encoding")
    except (OSError, zlib.error) as exc:
        raise FeedError("feed compression was invalid") from exc
    if len(result) > MAX_BODY_BYTES:
        raise FeedError("feed response exceeded the decompressed size limit")
    if content and len(result) > len(content) * MAX_COMPRESSION_RATIO:
        raise FeedError("feed response exceeded the compression-ratio limit")
    return result


def _bounded_zlib(content: bytes, window_bits: int) -> bytes:
    inflater = zlib.decompressobj(window_bits)
    result = inflater.decompress(content, MAX_BODY_BYTES + 1)
    if inflater.unconsumed_tail or len(result) > MAX_BODY_BYTES:
        raise FeedError("feed response exceeded the decompressed size limit")
    result += inflater.flush(MAX_BODY_BYTES + 1 - len(result))
    return result


def _retry_after_seconds(value: str | None) -> int:
    minimum = 15 * 60
    maximum = 24 * 60 * 60
    if value is None:
        return minimum
    try:
        seconds = int(value)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            seconds = int((retry_at - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            seconds = minimum
    return min(max(seconds, minimum), maximum)


def parse_feed(
    content: bytes,
    *,
    feed_url: str,
    resolver: Resolver = _default_resolver,
) -> ParsedFeed:
    """Parse at most 500 RSS/Atom entries with entities and DTDs disabled."""
    if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
        raise FeedError("feed XML declarations are not allowed")
    try:
        root = ElementTree.fromstring(content)
    except (ElementTree.ParseError, ValueError) as exc:
        raise FeedError("feed XML was malformed") from exc
    local = _local_name(root.tag)
    if local == "rss":
        channel = next((child for child in root if _local_name(child.tag) == "channel"), None)
        if channel is None:
            raise FeedError("RSS feed had no channel")
        title = _child_text(channel, "title")
        entries = [child for child in channel if _local_name(child.tag) == "item"]
        episodes = [_rss_episode(entry, feed_url, resolver) for entry in entries[:MAX_ENTRIES]]
    elif local == "feed":
        title = _child_text(root, "title")
        entries = [child for child in root if _local_name(child.tag) == "entry"]
        episodes = [_atom_episode(entry, feed_url, resolver) for entry in entries[:MAX_ENTRIES]]
    else:
        raise FeedError("XML document was not an RSS or Atom feed")
    return ParsedFeed(title=title, episodes=tuple(item for item in episodes if item is not None))


def _rss_episode(element: Element, feed_url: str, resolver: Resolver) -> FeedEpisode | None:
    guid = _child_text(element, "guid")
    title = _child_text(element, "title")
    published = _child_text(element, "pubDate")
    for child in element:
        if _local_name(child.tag) != "enclosure":
            continue
        media_type = (child.attrib.get("type") or "").lower()
        if not (media_type.startswith("audio/") or media_type.startswith("video/")):
            continue
        enclosure = _safe_enclosure(child.attrib.get("url"), feed_url, resolver)
        if enclosure:
            return _episode(guid, enclosure, title, published)
    return None


def _atom_episode(element: Element, feed_url: str, resolver: Resolver) -> FeedEpisode | None:
    guid = _child_text(element, "id")
    title = _child_text(element, "title")
    published = _child_text(element, "published") or _child_text(element, "updated")
    for child in element:
        if _local_name(child.tag) != "link" or child.attrib.get("rel", "alternate") != "enclosure":
            continue
        media_type = (child.attrib.get("type") or "").lower()
        if not (media_type.startswith("audio/") or media_type.startswith("video/")):
            continue
        enclosure = _safe_enclosure(child.attrib.get("href"), feed_url, resolver)
        if enclosure:
            return _episode(guid, enclosure, title, published)
    return None


def _safe_enclosure(value: str | None, feed_url: str, resolver: Resolver) -> str | None:
    if not value:
        return None
    try:
        canonical, _origin = validate_feed_url(urljoin(feed_url, value), resolver=resolver)
    except FeedError:
        return None
    return canonical


def _episode(
    guid: str | None,
    enclosure_url: str,
    title: str | None,
    published_at: str | None,
) -> FeedEpisode:
    bounded_guid = _bounded_text(guid)
    bounded_title = _bounded_text(title)
    bounded_published = _bounded_text(published_at)
    material = "\x1f".join((enclosure_url, bounded_published or "", bounded_title or ""))
    episode_key = bounded_guid or hashlib.sha256(material.encode()).hexdigest()
    return FeedEpisode(
        episode_key=episode_key,
        guid=bounded_guid,
        enclosure_url=enclosure_url,
        title=bounded_title,
        published_at=bounded_published,
    )


def _child_text(element: Element, name: str) -> str | None:
    for child in element:
        if _local_name(child.tag) == name:
            return _bounded_text("".join(child.itertext()))
    return None


def _bounded_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split())[:MAX_TEXT]
    return normalized or None


def _local_name(tag: str) -> str:
    return tag.rpartition("}")[2]
