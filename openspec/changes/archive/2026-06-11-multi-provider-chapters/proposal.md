# Multi-Provider Chapters (Desktop Phase 2)

## Why

Chapter generation is hardwired to the Anthropic SDK and the `ANTHROPIC_API_KEY` env var (`chapters.py:124-170`), but the desktop design requires bring-your-own-key across Anthropic, OpenAI, xAI, OpenRouter, DeepSeek, and custom endpoints — with keys held in engine memory (never on the engine's disk) so extension- and app-initiated jobs can generate chapters. A future hosted-inference offering must slot in as just another registry entry.

## What Changes

- `chapters.generate_chapters` is rewritten against the OpenAI-compatible `/chat/completions` request shape via `httpx`, parameterized by a provider registry entry `{base_url, default_model, key_env_var}` and an explicit API key. Anthropic goes through its OpenAI-compat endpoint — the `anthropic` SDK dependency is dropped entirely (one HTTP code path for all providers).
- New provider registry: `anthropic`, `openai`, `xai`, `openrouter`, `deepseek`, `custom` (user-supplied base URL). Universal prompt-and-parse output handling (JSON mode is not portable across providers in practice; the existing fence-stripping parse is).
- Pipeline chapters step takes `provider` + `api_key` from the request; CLI resolves the key from the provider's env var (`ANTHROPIC_API_KEY` unchanged as the default provider — full backward compatibility); a new `--provider` CLI flag selects the registry entry.
- Engine: new `PUT /v1/keys` endpoint storing `{provider: key}` in process memory only; `EngineSettings` gains `chapter_provider`; the job runner injects the in-memory key for the configured provider, **falling back to the provider's env var** so headless `serve` deployments keep working (per K1). Keys never appear in the journal, library, settings files, logs, or error messages.
- `pyproject.toml`: `httpx` becomes a core dependency; the `chapters` extra becomes an empty compatibility alias (chapters are now built-in with BYO key); `anthropic` is removed from core dependencies and from the `dev` extra (per K8).
- Truncation (`finish_reason == "length"`) and parse failures keep raising — the Phase 1 fault isolation already degrades them to chapterless transcripts with a structured warning.

No breaking changes: existing `ANTHROPIC_API_KEY`-based usage behaves identically on the CLI **and** the engine (env fallback, per K1); stale Phase 1 settings files load with defaults merged (per K3).

## Capabilities

### New Capabilities

- `chapter-providers`: provider registry, OpenAI-compatible request/response handling, key resolution from env (CLI) or injected key (engine), error behavior.
- `key-management`: engine in-memory key store — `PUT /v1/keys`, never-persisted guarantee, injection into jobs.

### Modified Capabilities

None in `openspec/specs/` (engine-extraction is not yet archived; its engine-service spec is extended here via the new `key-management` capability rather than a delta).

## Impact

- **Code:** `chapters.py` (request layer), new `providers.py` (registry), `pipeline.py` (chapters step signature), `cli.py` (`--provider`, `--model` default change, key resolution), `engine/app.py` (`/v1/keys` + `SettingsBody` new fields with defaults, per K3), `engine/settings.py` (+`chapter_provider`, defaults-merge on load), `engine/process.py` (key injection with env fallback), `types.py` (request fields).
- **Tests:** httpx mocked at the transport level (respx or httpx MockTransport — no network); key-redaction tests; registry tests; CLI env-var compatibility tests.
- **Docs:** README configuration table (per-provider env vars, `--provider`), CLAUDE.md module rows.
- **Deps:** +`httpx` (core), −`anthropic`.
