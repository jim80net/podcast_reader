# Multi-Provider Chapters — Design

## Context

Parent design: `docs/superpowers/specs/2026-06-11-desktop-packaging-design.md` v3 ("Multi-provider chapters", "Security model" F9/N-series). Phase 1 (engine-extraction) landed the fault-isolated chapters step, the engine job runner, and settings snapshotting — this change swaps the chapters step's LLM transport and adds key plumbing. Current code: `chapters.py` uses the `anthropic` SDK with an implicit env key; `pipeline.py` calls `generate_chapters(transcript_text, model=...)` inside the fault-isolated block.

## Goals / Non-Goals

**Goals:**
- One HTTP code path (OpenAI-compatible `/chat/completions`) for all six provider entries.
- Keys: CLI from env vars; engine from memory-only store; never persisted or logged by the engine.
- Identical behavior for existing `ANTHROPIC_API_KEY` users.

**Non-Goals:**
- Hosted-inference reseller entry (future registry addition).
- Streaming chapter generation, JSON-schema response enforcement (prompt-and-parse stays).
- Settings UI (Phase 3 consumes `PUT /v1/keys`).

## Decisions

1. **Drop the `anthropic` SDK; use `httpx` for everything.** Anthropic's OpenAI-compat endpoint (`<base>/v1/chat/completions`, `Authorization: Bearer sk-ant-…`) covers our single non-streaming completion call. Rationale: one request/parse/error path beats two; removes the optional-import dance and the `chapters` extra. Alternative (keep SDK for Anthropic, httpx for rest) rejected: doubles every test and failure mode for zero capability gain. Risk (compat endpoint drift) accepted: it is an officially documented surface, and the fault-isolation layer converts breakage into a chapterless transcript, not a failed job.
2. **Registry as data, not classes:** `providers.py` exposes `PROVIDERS: dict[str, ProviderSpec]` with `ProviderSpec` TypedDict `{base_url, default_model, key_env}`. `custom` reads its base URL from `EngineSettings.custom_provider_url` (CLI: `PODCAST_READER_CUSTOM_PROVIDER_URL`). Adding the hosted offering later = one dict entry.
3. **Key flow:** `PipelineRequest` gains `chapter_provider: str` and `chapter_api_key: str | None`. CLI resolves the key as `os.environ.get(spec.key_env)` at request build. Engine holds `dict[str, str]` on the FastAPI app state (`PUT /v1/keys {provider, api_key}`; key write-only — no GET); the runner injects `keys.get(settings.chapter_provider)` at job dequeue alongside the existing settings snapshot. No key in `JobRecord`, journal, library, or event payloads — the chapters step receives it as a function argument only.
4. **Skip-vs-fail semantics preserved:** no key for the selected provider → the existing `chapters_skipped` warning path (exactly today's missing-`ANTHROPIC_API_KEY` behavior); key present but request fails → `chapters_failed` warning path (Phase 1 fault isolation).
5. **Request shape:** `POST {base_url}/chat/completions` with `{model, max_tokens (provider-appropriate cap), messages: [system, user]}`; truncation = `choices[0].finish_reason == "length"`; content = `choices[0].message.content`; reuse the existing fence-strip + `json.loads`. Timeout 300 s; no retries (fault isolation handles failure; retries are a future nicety).
6. **Redaction discipline:** the httpx client is constructed per call with the key in a header; exception messages from httpx may embed the URL but never the key (Authorization headers are not in `repr(httpx.HTTPStatusError)`); the chapters step additionally wraps errors in a generic message before they reach the event/warning payload.

## Risks / Trade-offs

- [Provider compat endpoints differ subtly (max_tokens caps, finish_reason values)] → registry carries per-provider `max_tokens`; unknown finish_reasons treated as success-with-parse-attempt; fault isolation backstops.
- [Dropping `anthropic` changes installed-dep surface for existing users] → `chapters` extra kept as empty alias; README updated; behavior identical.
- [`custom` base URL is user-controlled SSRF-ish surface] → accepted: localhost trust model, the user configures their own machine; URL scheme validated (https or http://127.0.0.1/localhost).

## Migration Plan

Pure swap behind the chapters step. Existing env-var users see no change. Rollback = revert PR.

## Open Questions

None — provider list and semantics fixed by the parent design and brainstorm decisions.
