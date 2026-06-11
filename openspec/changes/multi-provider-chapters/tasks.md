# Multi-Provider Chapters — Tasks

## 1. Registry & transport

- [ ] 1.1 `src/podcast_reader/providers.py`: `ProviderSpec` TypedDict, `PROVIDERS` registry (anthropic/openai/xai/openrouter/deepseek/custom), custom-URL validation (https or localhost http); unit tests incl. validation scenarios
- [ ] 1.2 Rewrite `chapters.generate_chapters(transcript_text, *, spec, model, api_key)` on httpx `/chat/completions`: bearer auth, max-tokens cap from spec, finish_reason=="length" → truncation error, fence-strip parse unchanged; tests via `httpx.MockTransport` (success, truncation, HTTP error, no-anthropic-import); key-redaction test on error paths
- [ ] 1.3 `pyproject.toml`: httpx → core deps, remove anthropic, `chapters` extra → empty alias; `uv sync` + verify no anthropic import remains (`grep -r "import anthropic" src/`)

## 2. Pipeline & CLI

- [ ] 2.1 `types.py`: `PipelineRequest` + `chapter_provider`, `chapter_api_key`; `EngineSettings` + `chapter_provider`, `custom_provider_url`
- [ ] 2.2 `pipeline.py` chapters step: skip with `chapters_skipped` when key is None (replacing the env check), call new `generate_chapters` signature; existing fault-isolation tests updated; missing-key-skip test
- [ ] 2.3 `cli.py`: `--provider` flag (default anthropic), key resolution from registry env var at request build; env-var compatibility tests (ANTHROPIC_API_KEY exact behavior preserved; DEEPSEEK via flag)

## 3. Engine key store

- [ ] 3.1 `engine/app.py`: `PUT /v1/keys` (write-only, auth matrix coverage); in-memory store on app state; TestClient tests incl. keys-are-write-only sweep (no endpoint echoes the key)
- [ ] 3.2 `engine/settings.py` + `engine/process.py`: settings fields, runner injects `keys.get(chapter_provider)` at dequeue; restart-loses-keys test; no-key-in-journal/files sweep test (grep persisted files for the key after a run)

## 4. Docs, gates, integration

- [ ] 4.1 README (provider table, `--provider`, per-provider env vars, chapters-now-built-in note) + CLAUDE.md (providers.py row, chapters.py purpose update)
- [ ] 4.2 Full gates: pytest unit, mypy strict, ruff check+format; `openspec validate multi-provider-chapters`
- [ ] 4.3 Systems-review of implementation diff; PR stacked appropriately
