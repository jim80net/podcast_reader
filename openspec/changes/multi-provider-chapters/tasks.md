# Multi-Provider Chapters — Tasks

## 1. Registry & transport

- [x] 1.1 `src/podcast_reader/providers.py`: `ProviderSpec` TypedDict (`base_url, default_model, key_env, max_tokens`), `PROVIDERS` registry per the design table (verified base URLs/models; deepseek `deepseek-v4-flash` @ 8192, others 16384), custom-URL validation (https or localhost http); unit tests incl. validation scenarios
- [x] 1.2 Rewrite `chapters.generate_chapters(transcript_text, *, spec, model, api_key)` on httpx `/chat/completions`: bearer auth, `max_tokens=spec["max_tokens"]`, model=None → `spec["default_model"]` (per K2), finish_reason=="length" → truncation error, error messages exclude response bodies (per K4), fence-strip parse unchanged; tests via `httpx.MockTransport` (success, truncation, HTTP error, no-anthropic-import, default-model resolution); key-redaction test on error paths
- [x] 1.3 `pyproject.toml`: httpx → core deps, remove anthropic from dependencies AND the dev extra (per K8), `chapters` extra → empty alias; `uv sync` + verify no anthropic import remains (`grep -r "import anthropic" src/`)

## 2. Pipeline & CLI

- [x] 2.1 `types.py`: `PipelineRequest` + `chapter_provider`, `chapter_api_key`; `EngineSettings` + `chapter_provider`, `custom_provider_url`; `chapter_model` semantics: empty string = provider default
- [x] 2.2 `pipeline.py` chapters step: skip with `chapters_skipped` + provider-aware hint when key is None (per K8), generic-wrap all chapters exceptions before `_emit` so response-body content never reaches events/journal (per K4); existing fault-isolation tests updated; missing-key-skip test; operationalized redaction test (mocked 401 echoing the key → full key and key[:12] absent from all events and persisted files)
- [x] 2.3 `cli.py`: `--provider` flag (default anthropic), `--model` default → None meaning provider default + help text updated (per K2), key resolution from registry env var at request build; env-var compatibility tests (ANTHROPIC_API_KEY exact behavior preserved; DEEPSEEK via flag; provider flag without model uses provider default)

## 3. Engine key store

- [x] 3.1 Shared key store `dict[str, str]` created in `serve_engine`, passed to both `make_pipeline_runner` and `create_app` (per K7 — app.state can't host it; the runner closure is built first); `engine/app.py`: `PUT /v1/keys` (write-only, auth matrix coverage); TestClient tests incl. keys-are-write-only sweep (no endpoint echoes the key)
- [x] 3.2 `engine/settings.py`: new fields + defaults-merge in `load_settings` (per K3, stale Phase 1 file test); `engine/app.py` `SettingsBody` gains new fields with defaults (old-shape PUT test); `engine/process.py`: runner injects `keys.get(provider) or os.environ.get(spec.key_env)` at dequeue (per K1; env-fallback test + pushed-key-wins test); restart-loses-keys test; no-key-in-journal/files sweep test (grep persisted files for the key after a run)

## 4. Docs, gates, integration

- [x] 4.1 README (provider table, `--provider`, per-provider env vars, chapters-now-built-in note) + CLAUDE.md (providers.py row, chapters.py purpose update)
- [x] 4.2 Full gates: pytest unit, mypy strict, ruff check+format; `openspec validate multi-provider-chapters`
- [ ] 4.3 Systems-review of implementation diff; PR
