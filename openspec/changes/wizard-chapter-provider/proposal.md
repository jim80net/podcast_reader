# First-Run Chapter-Provider Onboarding

## Why

The first-run wizard sets up transcription (hardware + model packs) but never surfaces the chapter LLM — yet an AI provider is what produces **chapter markers and logical, idea-based paragraphs** (without one, `html.py` falls back to sentence-count grouping). Novices finish setup unaware of the single highest-value option. This adds an optional, guided chapter-provider section to the wizard.

## What Changes

- A new **optional "AI model" section** in the setup page (after Components): plain-language value prop (chapters, idea-based paragraphs, key points) + privacy reassurance, a provider dropdown (the 5 built-ins **plus** the existing `custom` base-URL option), a masked API-key field with a **Test** button, a per-provider "How do I get a key?" link, and a custom-URL field shown only for `custom`.
- **First run never blocks on a key**: Finish/Skip work with or without one; chapters just don't generate until a key exists (existing engine behavior). Saving stores the key (`PUT /v1/keys`) and sets the default provider / custom URL (`PUT /v1/settings`).
- Reuses the proven Settings provider/key flow (`listProviders`/`testKey`/`putKey`/`putSettings`) — **no new engine/IPC/boundary-type surface**, keys never persisted engine-side (Phase-2/3 model preserved).

Single-page section (not a multi-step wizard refactor). Arbitrary named endpoints + OAuth are out (issue #10, parked).

## Capabilities

### Modified Capabilities

- `app-setup-ui`: the first-run wizard gains an optional, guided chapter-provider step (pick provider, enter + test a key, or skip) so chapter generation and logical paragraphs are discoverable at onboarding.

## Impact

- **Code:** `app/src/renderer/src/views/setup.ts` (new section) reusing `settings-form` helpers; possible small shared extraction; a static provider→docs-URL map. No main-process/shared-types changes.
- **Tests:** vitest (provider→URL map, custom-URL toggle, save routing) + e2e (section renders, custom reveals URL field, Skip still finishes with no key). Existing wizard e2e stays green (additive, ids preserved).
- **Risk:** UI-only; reuses validated IPC. Main hazard — blocking first run on a key — is explicitly prevented.
