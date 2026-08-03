from __future__ import annotations

import gzip
from typing import TYPE_CHECKING, Any, cast

import pytest

from podcast_reader.engine.subscription_feed import (
    MAX_BODY_BYTES,
    FeedError,
    FeedTemporaryError,
    SafeFeedFetcher,
    _decompress,
    parse_feed,
    validate_feed_url,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


def _public(_host: str, _port: int) -> Sequence[str]:
    return ("93.184.216.34",)


@pytest.mark.parametrize(
    "url",
    [
        "http://feeds.example/show.xml",
        "https://user:pass@feeds.example/show.xml",
        "https://feeds.example:8443/show.xml",
        "https://feeds.example/show.xml#secret",
        "//feeds.example/show.xml",
    ],
)
def test_feed_url_rejects_unsafe_shapes(url: str) -> None:
    with pytest.raises(FeedError):
        validate_feed_url(url, resolver=_public)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.1.1",
        "224.0.0.1",
        "192.0.2.1",
        "::1",
        "fe80::1",
    ],
)
def test_feed_url_rejects_non_public_dns_answers(address: str) -> None:
    with pytest.raises(FeedError, match="public addresses"):
        validate_feed_url("https://feeds.example/show.xml", resolver=lambda _h, _p: (address,))


def test_feed_url_canonicalizes_host_and_origin() -> None:
    url, origin = validate_feed_url("https://FEEDS.Example/show.xml?private=1", resolver=_public)
    assert url == "https://feeds.example/show.xml?private=1"
    assert origin == "https://feeds.example"


def test_rss_parser_bounds_entries_and_accepts_only_safe_media_enclosures() -> None:
    items = "".join(
        f"""
        <item><guid>episode-{index}</guid><title> Episode {index} </title>
        <enclosure type="audio/mpeg" url="https://cdn.example/{index}.mp3"/></item>
        """
        for index in range(501)
    )
    feed = f"<rss><channel><title>Show</title>{items}</channel></rss>".encode()
    parsed = parse_feed(feed, feed_url="https://feeds.example/show.xml", resolver=_public)
    assert parsed.title == "Show"
    assert len(parsed.episodes) == 500
    assert parsed.episodes[0].episode_key == "episode-0"
    assert parsed.episodes[-1].episode_key == "episode-499"


def test_atom_parser_drops_private_and_non_media_enclosures() -> None:
    feed = b"""
    <feed xmlns="http://www.w3.org/2005/Atom"><title>Show</title>
      <entry><id>one</id><title>One</title>
        <link rel="enclosure" type="text/html" href="https://cdn.example/page"/>
        <link rel="enclosure" type="audio/mpeg" href="https://127.0.0.1/private.mp3"/>
      </entry>
    </feed>
    """
    parsed = parse_feed(feed, feed_url="https://feeds.example/show.xml", resolver=_public)
    assert parsed.episodes == ()


@pytest.mark.parametrize(
    "payload",
    [
        b'<!DOCTYPE rss [<!ENTITY steal SYSTEM "file:///etc/passwd">]><rss>&steal;</rss>',
        b"<not-a-feed/>",
        b"<rss>",
    ],
)
def test_parser_fails_closed_on_entities_and_malformed_or_wrong_xml(payload: bytes) -> None:
    with pytest.raises(FeedError):
        parse_feed(payload, feed_url="https://feeds.example/show.xml", resolver=_public)


def test_decompression_caps_output_and_ratio() -> None:
    compressed = gzip.compress(b"x" * (MAX_BODY_BYTES + 1))
    with pytest.raises(FeedError, match="decompressed size"):
        _decompress(compressed, "gzip")
    ratio_bomb = gzip.compress(b"x" * 100_000)
    with pytest.raises(FeedError, match="compression-ratio"):
        _decompress(ratio_bomb, "gzip")


def test_text_is_normalized_and_bounded() -> None:
    long_title = " word " * 2000
    feed = (
        "<rss><channel><item><guid>one</guid><title>"
        + long_title
        + "</title><enclosure type='audio/mpeg' url='https://cdn.example/one.mp3'/></item>"
        "</channel></rss>"
    ).encode()
    parsed = parse_feed(feed, feed_url="https://feeds.example/show.xml", resolver=_public)
    assert parsed.episodes[0].title is not None
    assert len(parsed.episodes[0].title or "") == 4096
    assert "  " not in (parsed.episodes[0].title or "")


class _Response:
    def __init__(self, status: int, headers: dict[str, str], content: bytes = b"") -> None:
        self.status = status
        self._headers = headers
        self._content = content
        self._offset = 0

    def getheader(self, name: str) -> str | None:
        return self._headers.get(name)

    def read(self, amount: int = -1) -> bytes:
        if amount < 0:
            amount = len(self._content) - self._offset
        result = self._content[self._offset : self._offset + amount]
        self._offset += len(result)
        return result


class _Connection:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.requests: list[tuple[str, str, dict[str, str]]] = []
        self.closed = False

    def request(self, method: str, target: str, *, headers: dict[str, str]) -> None:
        self.requests.append((method, target, headers))

    def getresponse(self) -> _Response:
        return self.response

    def close(self) -> None:
        self.closed = True


class _ConnectionFactory:
    def __init__(self, responses: list[_Response]) -> None:
        self._responses = responses
        self.connections: list[_Connection] = []

    def __call__(self, *_args: object, **_kwargs: object) -> _Connection:
        connection = _Connection(self._responses.pop(0))
        self.connections.append(connection)
        return connection


def _fetcher(responses: list[_Response]) -> tuple[SafeFeedFetcher, _ConnectionFactory]:
    factory = _ConnectionFactory(responses)
    fetcher = SafeFeedFetcher(
        resolver=_public,
        connection_factory=cast("Any", factory),
    )
    return fetcher, factory


def test_safe_fetcher_sends_only_feed_headers_and_preserves_query_in_target() -> None:
    response = _Response(
        200,
        {"Content-Type": "application/rss+xml", "ETag": '"v2"'},
        b"<rss><channel/></rss>",
    )
    fetcher, factory = _fetcher([response])
    result = fetcher.fetch(
        "https://feeds.example/show.xml?opaque=1",
        etag='"v1"',
        last_modified="Mon, 01 Jan 2024 00:00:00 GMT",
    )
    method, target, headers = factory.connections[0].requests[0]
    assert (method, target) == ("GET", "/show.xml?opaque=1")
    assert headers["If-None-Match"] == '"v1"'
    assert headers["If-Modified-Since"] == "Mon, 01 Jan 2024 00:00:00 GMT"
    assert {"Authorization", "Cookie", "Referer", "Proxy-Authorization"}.isdisjoint(headers)
    assert result.content == b"<rss><channel/></rss>"
    assert factory.connections[0].closed is True


def test_safe_fetcher_revalidates_redirect_and_rejects_https_downgrade() -> None:
    fetcher, factory = _fetcher([_Response(302, {"Location": "http://feeds.example/next"})])
    with pytest.raises(FeedError, match="absolute HTTPS"):
        fetcher.fetch("https://feeds.example/show.xml")
    assert len(factory.connections) == 1
    assert factory.connections[0].closed is True


def test_safe_fetcher_rejects_content_type_before_parsing() -> None:
    fetcher, _factory = _fetcher([_Response(200, {"Content-Type": "text/html"}, b"no")])
    with pytest.raises(FeedError, match="content type"):
        fetcher.fetch("https://feeds.example/show.xml")


@pytest.mark.parametrize(("value", "expected"), [("1", 900), ("999999", 86400)])
def test_safe_fetcher_clamps_retry_after(value: str, expected: int) -> None:
    fetcher, _factory = _fetcher([_Response(503, {"Retry-After": value})])
    with pytest.raises(FeedTemporaryError) as raised:
        fetcher.fetch("https://feeds.example/show.xml")
    assert raised.value.retry_after_seconds == expected


def test_safe_fetcher_observes_capability_cancellation_while_reading() -> None:
    response = _Response(
        200,
        {"Content-Type": "application/rss+xml"},
        b"<rss><channel/></rss>",
    )
    fetcher, _factory = _fetcher([response])
    checks = iter((True, True, False))
    with pytest.raises(FeedError, match="cancelled"):
        fetcher.fetch(
            "https://feeds.example/show.xml",
            should_continue=lambda: next(checks),
        )
