# Multi-Provider Chapters — Design

**Review history:** systems-review findings K1–K8 applied inline (engine env fallback,
model/provider precedence, stale-settings defaults merge, redaction ownership,
max-tokens table + verified registry data, compat-endpoint disclaimer, key-store
construction order, message/extras loose ends).

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

1. **Drop the `anthropic` SDK; use `httpx` for everything.** Anthropic's OpenAI-compat endpoint (`<base>/v1/chat/completions`, `Authorization: Bearer sk-ant-…` — the compat header table lists `authorization` as fully supported) covers our single non-streaming completion call; `response_format` is ignored there, which independently validates prompt-and-parse. Rationale: one request/parse/error path beats two; removes the optional-import dance. Alternative (keep SDK for Anthropic, httpx for rest) rejected: doubles every test and failure mode for zero capability gain. *(Per K6)* Risk accepted with eyes open: Anthropic's docs state the compat surface is "not considered a long-term or production-ready solution for most use cases… intended to test and compare model capabilities", while also promising no breaking changes; the fault-isolation layer converts any drift into a chapterless transcript, not a failed job.
2. **Registry as data, not classes** *(per K2, K5 — values verified against provider docs 2026-06-11)*: `providers.py` exposes `PROVIDERS: dict[str, ProviderSpec]` with `ProviderSpec` TypedDict `{base_url, default_model, key_env, max_tokens}`:

   | name | base_url | default_model | key_env | max_tokens |
   |------|----------|---------------|---------|------------|
   | anthropic | `https://api.anthropic.com/v1` | `claude-haiku-4-5-20251001` | `ANTHROPIC_API_KEY` | 16384 |
   | openai | `https://api.openai.com/v1` | `gpt-5.4-mini` | `OPENAI_API_KEY` | 16384 |
   | xai | `https://api.x.ai/v1` | `grok-4.3` | `XAI_API_KEY` | 16384 |
   | openrouter | `https://openrouter.ai/api/v1` | `anthropic/claude-haiku-4.5` | `OPENROUTER_API_KEY` | 16384 |
   | deepseek | `https://api.deepseek.com` | `deepseek-v4-flash` | `DEEPSEEK_API_KEY` | 8192 |
   | custom | from settings | from settings | `PODCAST_READER_CUSTOM_PROVIDER_KEY` | 16384 |

   (`deepseek-chat` is officially deprecated 2026-07-24 — not a valid default.) `custom` reads its base URL from `EngineSettings.custom_provider_url` (CLI: `PODCAST_READER_CUSTOM_PROVIDER_URL`). Adding the hosted offering later = one dict entry.
3. **Key flow** *(per K1, K7)*: `PipelineRequest` gains `chapter_provider: str` and `chapter_api_key: str | None`. CLI resolves the key as `os.environ.get(spec.key_env)` at request build. The engine key store is a plain `dict[str, str]` **created in `serve_engine` and passed to both `make_pipeline_runner` and `create_app`** (the runner closure is constructed before the app, so app state cannot host it). `PUT /v1/keys {provider, api_key}` writes it; no endpoint reads it back. *(Per K1)* The runner injects `keys.get(provider) or os.environ.get(spec.key_env)` at job dequeue — the env fallback preserves headless `podcast-reader serve` deployments that export `ANTHROPIC_API_KEY` today, keeping the no-breaking-changes claim true for the engine face. No key in `JobRecord`, journal, library, or event payloads — the chapters step receives it as a function argument only.
4. **Skip-vs-fail semantics preserved:** no key for the selected provider → the existing `chapters_skipped` warning path, with a provider-aware message *(per K8)*: "set <KEY_ENV> (or push a key via the app) to enable"; key present but request fails → `chapters_failed` warning path (Phase 1 fault isolation).
5. **Request shape & model precedence** *(per K2)*: `POST {base_url}/chat/completions` with `{model, max_tokens: spec.max_tokens, messages: [system, user]}`. Model resolution: explicit model if given, else `spec.default_model`. The CLI `--model` default becomes `None` ("provider default"; help text updated away from "Claude model"), and `EngineSettings.chapter_model` defaults to `""` meaning provider default — switching providers without touching the model never sends an Anthropic model id to DeepSeek. Truncation = `choices[0].finish_reason == "length"`; content = `choices[0].message.content`; reuse the existing fence-strip + `json.loads`; unknown finish_reason values are treated as success-with-parse-attempt. Timeout 300 s; no retries (fault isolation handles failure).
6. **Redaction discipline** *(per K4)*: verified on httpx 0.28: `Headers.__repr__` redacts `authorization` and `str(HTTPStatusError)` carries status + URL only — but the real leak vector is the provider's *response body* (OpenAI-style 401 bodies echo key fragments). Therefore `generate_chapters` raises errors whose messages never include response bodies, and the **pipeline chapters step owns a second layer**: it wraps any exception in a generic message before `_emit`, so nothing body-derived reaches events or the journal. Operationalized test (task 2.2): after a mocked 401 whose body echoes the key, neither the full key nor its first 12 characters appear in any emitted event or persisted file.
7. **Stale Phase 1 state upgrades cleanly** *(per K3)*: `load_settings` merges loaded JSON over `default_settings()` so a Phase 1 `settings.json` lacking the new fields cannot `KeyError` the runner outside the fault-isolated block; `SettingsBody` (the Pydantic mirror in `engine/app.py` that `PUT /v1/settings` validates) gains the new fields **with defaults** so existing clients' PUTs keep returning 200.

## Risks / Trade-offs

- [Provider compat endpoints differ subtly (max_tokens caps, finish_reason values)] → registry carries per-provider `max_tokens`; unknown finish_reasons treated as success-with-parse-attempt; fault isolation backstops.
- [Dropping `anthropic` changes installed-dep surface for existing users] → `chapters` extra kept as empty alias; README updated; behavior identical.
- [`custom` base URL is user-controlled SSRF-ish surface] → accepted: localhost trust model, the user configures their own machine; URL scheme validated (https or http://127.0.0.1/localhost).

## Migration Plan

Pure swap behind the chapters step. Existing env-var users see no change. Rollback = revert PR.

## Open Questions

None — provider list and semantics fixed by the parent design and brainstorm decisions.
