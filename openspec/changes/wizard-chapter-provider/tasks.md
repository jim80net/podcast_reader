# Tasks — First-Run Chapter-Provider Onboarding

Restyle/extend; preserve existing wizard DOM ids/e2e. From `app/`: typecheck/lint/test/build/e2e.

## 1. Chapter-provider section (setup.ts)
- [x] 1.1 Add an optional "AI model" section after Components: value-prop copy + 3 benefits + privacy line.
- [x] 1.2 Provider `<select>` from `listProviders()` (built-ins + `custom`); key-available hints.
- [x] 1.3 Custom base-URL input shown only when provider === 'custom' (mirror settings.ts).
- [x] 1.4 Masked API-key input + Test (testKey) + inline result; Save → putKey + putSettings(provider, custom_provider_url).
- [x] 1.5 Per-provider "How do I get a key?" link (static provider→URL map; missing → no link).
- [x] 1.6 Skip/Finish never require a key (no-block guarantee); section independent of pack-install state.

## 2. Tests
- [x] 2.1 vitest: provider→URL map (pure), custom-URL toggle, Save routing (mock window.api).
- [x] 2.2 e2e: AI section renders; custom reveals URL field; Skip finishes setup with no key set.

## 3. Docs
- [x] 3.1 app/README.md (setup wizard) + CLAUDE.md row note the chapter-provider onboarding.
