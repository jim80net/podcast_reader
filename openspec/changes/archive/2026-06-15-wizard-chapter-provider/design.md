# Design

Full design: `docs/superpowers/specs/2026-06-15-wizard-chapter-provider-design.md`. Key points: an
optional single-page "AI model" section in `setup.ts`, reusing the Settings `listProviders`/`testKey`/
`putKey`/`putSettings` flow; built-ins + the legacy `custom` base-URL slot; first run never blocks on
a key (Skip/Finish work without one); no new IPC/engine surface; keys stay vault/engine-memory only.
OAuth + arbitrary named endpoints are issue #10 (parked).
