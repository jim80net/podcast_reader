# User-Defined Chapter Providers — Design

**Date:** 2026-06-14
**Status:** Approved brainstorm, pre-systems-review
**Author:** Jim Park, with Claude
**Review history:** v1 — approved in brainstorm. Scope: any number of named,
user-defined OpenAI-compatible chapter endpoints, first-class in the engine, the
desktop Settings, `/v1/providers`, and the CLI. OAuth sign-in is **out** (deferred —
the credentialed half of issue #10). URL policy confirmed: keep https-or-localhost.

## Problem

Chapter generation supports a fixed built-in registry (anthropic/openai/xai/openrouter/
deepseek) plus exactly **one** `custom` slot (`EngineSettings.custom_provider_url` +
`PODCAST_READER_CUSTOM_PROVIDER_URL`, a single key). Issue #10 asks for **arbitrary named
endpoints** — OpenCode Zen, a local llama.cpp server, a corporate gateway — each with its
own base URL, default model, max-tokens cap, and key. This change generalizes the single
custom slot into a user-defined provider list, built directly on the Phase 2 registry
(`providers.py`) and the Phase 3 key vault / Settings view.

## Goals

- Add, edit, remove **any number** of named OpenAI-compatible endpoints from desktop Settings.
- Each user endpoint has `{name, base_url, default_model, max_tokens}` (non-secret) plus its
  own key (vault + engine-memory, never persisted engine-side).
- Built-in registry stays the closed set of defaults; user entries are additive.
- CLI `--provider <name>` resolves user entries too.
- Preserve every Phase 2/3 invariant: key-redaction discipline (K4), keys-never-persisted-
  engine-side, and the https-or-localhost URL policy.

## Non-goals (out of scope)

- **OAuth / device-code sign-in** (xAI/Grok, ChatGPT/Codex subscription auth) — deferred;
  it is the credentialed half of #10 and needs its own change.
- Per-entry "allow insecure http" opt-in — a possible future follow-on; v1 keeps the
  https-or-localhost policy.

## Data model

```
UserProvider (non-secret, persisted):  { name, base_url, default_model, max_tokens }
EngineSettings.user_providers: list[UserProvider]   # NEW
EngineSettings.custom_provider_url: str             # RETAINED for migration, then unused
```

- Keys are **not** in `UserProvider`. They stay per-provider-**name** in the app vault and
  the engine's in-memory key store (already name-keyed), pushed via `PUT /v1/keys`.
- **Migration on load** (`settings.py`): a non-empty legacy `custom_provider_url` becomes a
  `user_providers` entry named `custom` (its key already lives under `custom`), so existing
  configs and `chapter_provider == "custom"` keep working. The built-in `custom` registry
  entry is retired (it only ever materialized from config).

## Resolution (`providers.py`)

- `ProviderSpec` is unchanged. A user entry is materialized into a `ProviderSpec` at resolve
  time: `key_env` is **derived from the name** (`<NAME>_API_KEY`, upper-cased, non-alnum →
  `_`; `custom` keeps `PODCAST_READER_CUSTOM_PROVIDER_KEY` for back-compat) for the CLI/env
  path; the engine path uses the pushed key keyed by name.
- `resolve_provider(name, *, user_providers)` returns the built-in spec, or a user entry
  materialized through `validate_user_provider`, or raises for an unknown name.
- **Name rules (validated on save):** non-empty, unique, and must not collide with a
  built-in name — so `chapter_provider` is always unambiguous and a user entry can never
  silently shadow a built-in.
- `validate_user_provider` reuses the existing URL policy: `base_url` must be **https, or
  http on localhost/127.0.0.1** (the confirmed decision); `max_tokens` positive;
  `default_model` may be empty (means "the endpoint's own default", same as today).

## Engine API (`app.py`)

- `GET /v1/providers` returns built-ins **and** user entries, each with `id`,
  `default_model`, and `key_available` (boolean only — never key material). `key_available`
  mirrors job-time resolution per name, exactly as today.
- `PUT /v1/settings` accepts `user_providers` (validated; invalid entries → 4xx with a
  self-authored message). Settings remain non-secret; keys ride the separate `PUT /v1/keys`.
- No new route — this rides the existing settings + providers + keys surface.

## Desktop Settings UI

- Replace the single "Custom provider base URL" field with a **"Custom endpoints" list
  editor**: rows of `name · base_url · default_model · max_tokens`, each with add/edit/
  remove and a write-only masked key entry + the existing engine-side "Test key" button
  (per name).
- The provider dropdown lists built-ins + user entries (the dropdown already renders from
  `/v1/providers`, so it picks these up once the engine returns them).
- Client-side mirror of the name/URL validation for immediate feedback; the engine is the
  source of truth (re-validates on PUT).

## CLI (`cli.py`)

- `--provider` accepts a built-in name or a user-defined name. When the name isn't a
  built-in, the CLI loads `user_providers` from the shared engine settings file
  (`<data_dir>/settings.json`) and resolves it; the key comes from the derived env var.
  `--provider` choices/help reflect both sets. The existing
  `PODCAST_READER_CUSTOM_PROVIDER_URL/_KEY` env path keeps working via the migrated `custom`
  entry.

## Security

- Keys: per-name, vault + engine-memory, never persisted engine-side; K4 redaction
  (no key material in events/journal/logs) is unchanged — `user_providers` is non-secret
  config only.
- URL policy: https-or-localhost for every user endpoint, so a key is never sent in
  cleartext to a remote host (the localhost carve-out covers local servers/gateways).
- `key_available` stays a boolean; no endpoint to read key material back.

## Testing

- **Python unit:** `resolve_provider` over built-in + user names; unique/no-shadow name
  validation; URL policy (https / http-localhost / reject); legacy `custom_provider_url` →
  `user_providers` migration; the name→`key_env` derivation; `/v1/providers` includes user
  entries with correct `key_available`; `PUT /v1/settings` validates `user_providers`.
- **App:** the list-editor add/edit/remove + per-entry key; key-set parity for the new TS
  types (`UserProvider`, `EngineSettings.user_providers`, `SettingsUpdate`); the dropdown
  rendering user entries.
- **Mock-engine + e2e:** add a user endpoint, set its key (test passes), select it for
  chapters; the isolation key-set assertion if `window.api` changes (it shouldn't).

## Forward compatibility (not built here)

The `{name, base_url, default_model}` + opaque-bearer-key abstraction is deliberately the
seam a future **hosted/subscription provider** ("Podcast Reader Cloud") slots into: a
managed endpoint is just another entry pointing at our backend, with server-side caching,
RSS subscriptions, and the iOS app riding that same boundary, and the deferred OAuth flow
becoming its sign-in. Nothing here builds that — but the design keeps `ProviderSpec`/
resolution provider-kind-agnostic so a `kind: 'hosted'` (or similar) can be added later
without reworking the registry.

## Risks & mitigations

| Risk | Mitigation |
|------|-----------|
| A user name shadows/collides with a built-in | validated unique + no-built-in-collision on save |
| Key sent in cleartext to a remote http endpoint | https-or-localhost policy enforced engine-side |
| Legacy `custom` configs break | explicit on-load migration to a `custom` user entry; key stays under `custom` |
| Key material leaking into config/logs | `user_providers` is non-secret; keys stay in the vault/engine store; K4 sweep tests |
| CLI/engine settings-format coupling | CLI reads the same `settings.json` the engine writes; one schema, one validator |
