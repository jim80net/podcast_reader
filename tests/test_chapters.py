"""Tests for chapter generation and timestamp snapping in podcast_reader.chapters."""

from __future__ import annotations

import json
import sys
from typing import Any

import httpx
import pytest

from podcast_reader.chapters import (
    KEY_TEST_TIMEOUT_S,
    SYSTEM_PROMPT,
    ChapterError,
    generate_chapters,
    snap_chapters_to_segments,
    verify_key,
)
from podcast_reader.providers import PROVIDERS, ProviderSpec


def _ch(
    title: str = "Ch1",
    start: float = 0,
    end: float = 30,
    paragraph_breaks: list[float] | None = None,
    pull_quote: str | None = None,
    pull_quote_start: float | None = None,
) -> dict[str, Any]:
    """Build a chapter dict with sensible defaults."""
    return {
        "title": title,
        "start": start,
        "end": end,
        "abstract": "",
        "type": "content",
        "paragraph_breaks": paragraph_breaks or [start],
        "key_points": [],
        "pull_quote": pull_quote,
        "pull_quote_start": pull_quote_start,
    }


class TestSnapChaptersToSegments:
    """Verify that LLM-generated chapter timestamps are snapped to real segment timestamps."""

    SEGMENTS: list[dict[str, Any]] = [
        {"start": 0.0, "end": 5.0, "text": "Hello."},
        {"start": 5.0, "end": 10.0, "text": "Topic one."},
        {"start": 10.0, "end": 15.0, "text": "More topic one."},
        {"start": 15.0, "end": 20.0, "text": "Topic two begins."},
        {"start": 20.0, "end": 25.0, "text": "Topic two continues."},
        {"start": 25.0, "end": 30.0, "text": "Final words."},
    ]

    def test_snaps_start_to_nearest_segment(self) -> None:
        """Chapter start not matching any segment gets snapped to nearest."""
        chapters = [
            _ch(start=0, end=12, paragraph_breaks=[0]),
            _ch(title="Ch2", start=17, end=30, paragraph_breaks=[17]),
        ]
        result = snap_chapters_to_segments(chapters, self.SEGMENTS)
        # 17 is closest to segment at 15.0
        assert result[1]["start"] == 15.0
        assert result[1]["paragraph_breaks"][0] == 15.0

    def test_snaps_end_to_nearest_segment(self) -> None:
        """Chapter end not matching any segment gets snapped to nearest."""
        chapters = [_ch(start=0, end=12, paragraph_breaks=[0])]
        result = snap_chapters_to_segments(chapters, self.SEGMENTS)
        # 12 is closest to segment at 10.0
        assert result[0]["end"] == 10.0

    def test_snaps_paragraph_breaks(self) -> None:
        """paragraph_breaks timestamps are snapped to nearest segments."""
        chapters = [_ch(paragraph_breaks=[0, 11, 22])]
        result = snap_chapters_to_segments(chapters, self.SEGMENTS)
        assert result[0]["paragraph_breaks"] == [0.0, 10.0, 20.0]

    def test_snaps_pull_quote_start(self) -> None:
        """pull_quote_start is snapped to nearest segment."""
        chapters = [
            _ch(
                pull_quote="Topic two begins.",
                pull_quote_start=16,
            )
        ]
        result = snap_chapters_to_segments(chapters, self.SEGMENTS)
        assert result[0]["pull_quote_start"] == 15.0

    def test_exact_timestamps_unchanged(self) -> None:
        """Timestamps that already match segments are not modified."""
        chapters = [_ch(start=0.0, end=15.0, paragraph_breaks=[0.0, 10.0])]
        result = snap_chapters_to_segments(chapters, self.SEGMENTS)
        assert result[0]["start"] == 0.0
        assert result[0]["end"] == 15.0
        assert result[0]["paragraph_breaks"] == [0.0, 10.0]

    def test_hallucinated_timestamp_beyond_transcript(self) -> None:
        """Timestamp beyond last segment snaps to last segment."""
        chapters = [
            _ch(title="Earlier", start=0, end=24, paragraph_breaks=[0]),
            _ch(
                title="Beyond transcript",
                start=35,
                end=50,
                paragraph_breaks=[35],
            ),
        ]
        result = snap_chapters_to_segments(chapters, self.SEGMENTS)
        # 35 and 50 are beyond last segment (25.0), should snap to 25.0
        assert result[1]["start"] == 25.0
        assert result[1]["end"] == 25.0

    def test_empty_chapters(self) -> None:
        """Empty chapter list returns empty."""
        assert snap_chapters_to_segments([], self.SEGMENTS) == []

    def test_empty_segments(self) -> None:
        """No segments means no snapping — returns chapters unchanged."""
        chapters = [_ch(start=10, end=20, paragraph_breaks=[10])]
        result = snap_chapters_to_segments(chapters, [])
        assert result[0]["start"] == 10


_CHAPTERS_JSON = [
    {
        "title": "Intro",
        "start": 0.0,
        "end": 5.0,
        "abstract": "Opening.",
        "type": "intro",
        "paragraph_breaks": [0.0],
        "key_points": [],
        "pull_quote": None,
        "pull_quote_start": None,
    }
]


def _completion(content: str, finish_reason: str = "stop") -> dict[str, Any]:
    """Build an OpenAI-compatible /chat/completions response body."""
    return {"choices": [{"finish_reason": finish_reason, "message": {"content": content}}]}


class _Recorder:
    """MockTransport handler that records requests and replays a response."""

    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.response

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


class TestGenerateChapters:
    """Spec: OpenAI-compatible generation — one HTTP path for every provider."""

    SPEC: ProviderSpec = PROVIDERS["anthropic"]

    def test_successful_generation_returns_parsed_chapters(self) -> None:
        recorder = _Recorder(httpx.Response(200, json=_completion(json.dumps(_CHAPTERS_JSON))))
        chapters = generate_chapters(
            "[0.0] Hello.",
            spec=self.SPEC,
            api_key="sk-test",
            transport=recorder.transport,
        )
        assert chapters == _CHAPTERS_JSON

    def test_request_shape_matches_chat_completions_contract(self) -> None:
        recorder = _Recorder(httpx.Response(200, json=_completion(json.dumps(_CHAPTERS_JSON))))
        generate_chapters(
            "[0.0] Hello.",
            spec=self.SPEC,
            api_key="sk-test",
            transport=recorder.transport,
        )
        request = recorder.requests[0]
        assert str(request.url) == "https://api.anthropic.com/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer sk-test"
        payload = json.loads(request.content)
        assert payload["max_tokens"] == self.SPEC["max_tokens"]
        assert payload["messages"][0] == {"role": "system", "content": SYSTEM_PROMPT}
        assert payload["messages"][1]["role"] == "user"
        assert "[0.0] Hello." in payload["messages"][1]["content"]

    def test_model_none_resolves_to_provider_default(self) -> None:
        """Spec: Model precedence — no explicit model means the provider default."""
        recorder = _Recorder(httpx.Response(200, json=_completion(json.dumps(_CHAPTERS_JSON))))
        generate_chapters(
            "[0.0] Hello.",
            spec=PROVIDERS["deepseek"],
            api_key="sk-test",
            transport=recorder.transport,
        )
        payload = json.loads(recorder.requests[0].content)
        assert payload["model"] == "deepseek-v4-flash"
        assert str(recorder.requests[0].url) == "https://api.deepseek.com/chat/completions"

    def test_explicit_model_passes_through_verbatim(self) -> None:
        recorder = _Recorder(httpx.Response(200, json=_completion(json.dumps(_CHAPTERS_JSON))))
        generate_chapters(
            "[0.0] Hello.",
            spec=PROVIDERS["openrouter"],
            model="meta-llama/llama-4-maverick",
            api_key="sk-test",
            transport=recorder.transport,
        )
        payload = json.loads(recorder.requests[0].content)
        assert payload["model"] == "meta-llama/llama-4-maverick"

    def test_markdown_fences_stripped_before_parse(self) -> None:
        fenced = "```json\n" + json.dumps(_CHAPTERS_JSON) + "\n```"
        recorder = _Recorder(httpx.Response(200, json=_completion(fenced)))
        chapters = generate_chapters(
            "[0.0] Hello.",
            spec=self.SPEC,
            api_key="sk-test",
            transport=recorder.transport,
        )
        assert chapters == _CHAPTERS_JSON

    def test_fenced_content_without_trailing_newline_parses(self) -> None:
        fenced = "```json\n" + json.dumps(_CHAPTERS_JSON) + "```"
        recorder = _Recorder(httpx.Response(200, json=_completion(fenced)))
        chapters = generate_chapters(
            "[0.0] Hello.",
            spec=self.SPEC,
            api_key="sk-test",
            transport=recorder.transport,
        )
        assert chapters == _CHAPTERS_JSON

    @pytest.mark.parametrize("tag", ["json", ""])
    def test_single_line_fenced_payload_parses(self, tag: str) -> None:
        """A whole fenced response on ONE line must keep its payload — the
        no-newline branch must not discard it (cubic P2 on the OCR fix)."""
        fenced = f"```{tag} " + json.dumps(_CHAPTERS_JSON) + "```"
        recorder = _Recorder(httpx.Response(200, json=_completion(fenced)))
        chapters = generate_chapters(
            "[0.0] Hello.",
            spec=self.SPEC,
            api_key="sk-test",
            transport=recorder.transport,
        )
        assert chapters == _CHAPTERS_JSON

    @pytest.mark.parametrize("content", ["```json", "```", ""])
    def test_fence_only_or_blank_content_raises_chapter_error(self, content: str) -> None:
        """A single-line fence (no newline) must not raise IndexError — it is
        an empty response, reported with a self-authored ChapterError."""
        recorder = _Recorder(httpx.Response(200, json=_completion(content)))
        with pytest.raises(ChapterError, match="empty response"):
            generate_chapters(
                "[0.0] Hello.",
                spec=self.SPEC,
                api_key="sk-test",
                transport=recorder.transport,
            )

    def test_null_content_raises_chapter_error(self) -> None:
        """message.content: null must not parse as the string 'None'."""
        body = {"choices": [{"finish_reason": "stop", "message": {"content": None}}]}
        recorder = _Recorder(httpx.Response(200, json=body))
        with pytest.raises(ChapterError, match="empty response"):
            generate_chapters(
                "[0.0] Hello.",
                spec=self.SPEC,
                api_key="sk-test",
                transport=recorder.transport,
            )

    def test_non_json_response_raises_chapter_error_without_body(self) -> None:
        """An HTML gateway page (custom provider misconfiguration) must produce
        a self-authored diagnosis — and never echo the response body."""
        recorder = _Recorder(httpx.Response(200, text="<html>secret-fragment Bad Gateway</html>"))
        with pytest.raises(ChapterError, match="unexpected response format") as excinfo:
            generate_chapters(
                "[0.0] Hello.",
                spec=self.SPEC,
                api_key="sk-test",
                transport=recorder.transport,
            )
        assert "secret-fragment" not in str(excinfo.value)

    @pytest.mark.parametrize(
        "body",
        [
            {},  # missing choices
            {"choices": []},  # empty choices
            {"choices": [{"finish_reason": "stop"}]},  # missing message
            [],  # JSON array instead of object
        ],
    )
    def test_malformed_envelope_raises_chapter_error(self, body: Any) -> None:
        recorder = _Recorder(httpx.Response(200, json=body))
        with pytest.raises(ChapterError, match="unexpected response format"):
            generate_chapters(
                "[0.0] Hello.",
                spec=self.SPEC,
                api_key="sk-test",
                transport=recorder.transport,
            )

    def test_truncation_raises_chapter_error(self) -> None:
        """Spec: Truncation raises — finish_reason 'length' is a ChapterError
        whose self-authored message may be surfaced verbatim (M2)."""
        recorder = _Recorder(
            httpx.Response(200, json=_completion('[{"title": "cut', finish_reason="length"))
        )
        with pytest.raises(ChapterError, match="truncated"):
            generate_chapters(
                "[0.0] Hello.",
                spec=self.SPEC,
                api_key="sk-test",
                transport=recorder.transport,
            )

    def test_unknown_finish_reason_treated_as_success(self) -> None:
        """Per design: unknown finish_reason values are success-with-parse-attempt."""
        recorder = _Recorder(
            httpx.Response(
                200, json=_completion(json.dumps(_CHAPTERS_JSON), finish_reason="end_turn")
            )
        )
        chapters = generate_chapters(
            "[0.0] Hello.",
            spec=self.SPEC,
            api_key="sk-test",
            transport=recorder.transport,
        )
        assert chapters == _CHAPTERS_JSON

    def test_http_error_raises_without_response_body(self) -> None:
        """Spec: Key redaction — error messages exclude the response body."""
        api_key = "sk-test-secret-key-123456789"
        body = {"error": {"message": f"Incorrect API key provided: {api_key}"}}
        recorder = _Recorder(httpx.Response(401, json=body))
        with pytest.raises(RuntimeError, match="HTTP 401") as excinfo:
            generate_chapters(
                "[0.0] Hello.",
                spec=self.SPEC,
                api_key=api_key,
                transport=recorder.transport,
            )
        message = str(excinfo.value)
        assert api_key not in message
        assert api_key[:12] not in message
        assert "Incorrect API key" not in message

    def test_verify_key_sends_minimal_completion(self) -> None:
        """verify_key is one tiny /chat/completions round-trip — same transport
        as generate_chapters, but max_tokens=1 and no transcript."""
        recorder = _Recorder(httpx.Response(200, json=_completion("ok")))
        verify_key(spec=self.SPEC, api_key="sk-test", transport=recorder.transport)
        request = recorder.requests[0]
        assert str(request.url) == "https://api.anthropic.com/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer sk-test"
        payload = json.loads(request.content)
        assert payload["model"] == self.SPEC["default_model"]
        assert payload["max_tokens"] == 1
        assert SYSTEM_PROMPT not in json.dumps(payload)
        assert KEY_TEST_TIMEOUT_S < 300  # a key test must not wait like a transcript

    def test_verify_key_explicit_model_passes_through(self) -> None:
        recorder = _Recorder(httpx.Response(200, json=_completion("ok")))
        verify_key(
            spec=PROVIDERS["openrouter"],
            api_key="sk-test",
            model="meta-llama/llama-4-maverick",
            transport=recorder.transport,
        )
        assert json.loads(recorder.requests[0].content)["model"] == "meta-llama/llama-4-maverick"

    def test_verify_key_http_error_raises_without_response_body(self) -> None:
        """K4 redaction: the auth-error body (which echoes the key) never
        reaches the exception message."""
        api_key = "sk-verify-secret-key-123456789"
        body = {"error": {"message": f"Incorrect API key provided: {api_key}"}}
        recorder = _Recorder(httpx.Response(401, json=body))
        with pytest.raises(RuntimeError, match="HTTP 401") as excinfo:
            verify_key(spec=self.SPEC, api_key=api_key, transport=recorder.transport)
        message = str(excinfo.value)
        assert api_key not in message
        assert "Incorrect API key" not in message

    def test_no_anthropic_import_required(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Spec: No anthropic import — generation succeeds with the package absent."""
        monkeypatch.setitem(sys.modules, "anthropic", None)  # import anthropic -> ImportError
        recorder = _Recorder(httpx.Response(200, json=_completion(json.dumps(_CHAPTERS_JSON))))
        chapters = generate_chapters(
            "[0.0] Hello.",
            spec=self.SPEC,
            api_key="sk-test",
            transport=recorder.transport,
        )
        assert chapters == _CHAPTERS_JSON
