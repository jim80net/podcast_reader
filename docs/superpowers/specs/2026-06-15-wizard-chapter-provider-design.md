# First-Run Chapter-Provider Onboarding — Design

**Date:** 2026-06-15
**Status:** Approved (mockup confirmed), pre-implementation
**Author:** Jim Park, with Claude
**Review history:** v1 — direction confirmed from a rendered mockup. Decisions: a **single-page
section** added to the existing setup wizard (not a multi-step refactor); **optional/skippable**;
show the 5 built-in providers **plus** the existing `custom` base-URL option. Goal: make chapter
setup genuinely helpful to novices.

## Problem

The first-run wizard sets up transcription (hardware + model packs) but never mentions the chapter
LLM. Yet an AI provider is what gives **chapter markers AND logical, idea-based paragraphs** (without
one, `html.py` falls back to the sentence-count heuristic). Novices finish setup not knowing the
single highest-value option exists, or how to enable it. This adds an optional, guided chapter-
provider section to the wizard.

## Goals

- An **optional** "AI model" section in the setup page (after Components) that, in plain language,
  explains what an AI model buys you (chapters, idea-based paragraphs, key points), lets you pick a
  provider, paste + **Test** a key, and continue — or skip and add it later in Settings.
- **First run never blocks on a key** — Finish/Skip work with or without one; chapters simply don't
  generate until a key exists (existing engine behavior).
- Reuse the proven Settings provider/key flow — no new engine/IPC surface.

## Non-goals

- A multi-step wizard refactor (chosen: single page + section).
- OAuth sign-in and arbitrary *named* endpoints — the parked issue #10. (The single legacy `custom`
  base-URL slot IS included, since it already exists.)

## Component (`setup.ts`, new section; reuses Settings patterns)

A new `chapterSection` between Components and the action buttons:
- **Value-prop copy:** "Make it smarter with an AI model (optional)" + the three benefits (chapters,
  idea-based paragraphs, key points), and a privacy line ("stored encrypted on this device; sent only
  to the provider you pick, never to us").
- **Provider `<select>`** fed by `window.api.listProviders()` (the 5 built-ins + `custom`) — the same
  source the Settings dropdown uses.
- **Custom base-URL input**, shown only when `provider === 'custom'` (mirrors `settings.ts:140`),
  persisted via `putSettings({ ...settings, custom_provider_url })`.
- **API-key input (masked) + "Test" button + result**, using `window.api.testKey(provider, key)` and
  storing via `window.api.putKey(provider, key)` — identical to Settings' key flow (write-only, never
  read back; the masked-placeholder convention from `settings-form.ts`).
- **A "How do I get a key?" link** per provider (a small static map of provider → docs URL; opens in
  the browser via the existing external-link affordance, or shows the URL as text if none).
- On a successful save, also `putSettings({ ...settings, chapter_provider })` so the chosen provider
  is the default. Skipping leaves settings untouched.

The section sits inside the existing single-scroll page; the Finish/Skip buttons (and their gating
from the polish batch) are unchanged — chapter setup is independent of pack install state.

## Data flow

`listProviders` → render dropdown (+ `key_available` hints) → user enters key → `testKey` (engine
round-trip via `POST /v1/keys/test`) → on success `putKey` (engine memory + vault) + `putSettings`
(provider/custom URL). All existing IPC; no boundary-type change. Keys never persisted engine-side
(Phase-2/3 model); K4 redaction unaffected (no key material in this view's logs).

## Error handling

- Test failure → inline result message (reuse the Settings `keyResult` pattern); never blocks Finish.
- `listProviders`/`putSettings` failure → inline error; the section degrades to "set this up later in
  Settings" rather than trapping the wizard.

## Testing

- **vitest:** the provider→docs-URL map (pure), the "custom shows URL field" toggle, and that Save
  routes key→`putKey` + provider→`putSettings` (mock `window.api`), mirroring the Settings tests.
- **e2e:** in the setup flow, the AI section renders with the provider dropdown; selecting `custom`
  reveals the URL field; **Skip still finishes setup** with no key set (the no-block guarantee). Reuse
  the mock engine's providers/keys endpoints.
- typecheck/lint/build green; the existing wizard e2e stays green (section is additive, ids preserved).

## Risks & mitigations

| Risk | Mitigation |
|------|-----------|
| First run blocks without a key | Finish/Skip never require a key; chapter gen is optional |
| Duplicates Settings logic | reuse the same `listProviders`/`testKey`/`putKey` IPC + the `settings-form` helpers; factor shared bits if it reduces duplication |
| "How to get a key" links rot | a small static provider→URL map, easy to update; missing entry → no link, not a broken one |
| Custom URL validation | the engine already validates (https-or-localhost) on `putSettings`; surface its error inline |

## Follow-ons (tracked)

1. Arbitrary named endpoints + OAuth sign-in — issue #10 (parked design on `feature/user-defined-providers`).
2. Multi-step wizard, if onboarding grows.
