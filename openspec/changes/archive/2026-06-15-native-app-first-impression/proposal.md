# Native App First Impression

## Why

The packaged desktop app has no visual identity: `app/build/` shipped no icon, so the
installer, macOS dock/.app, Windows taskbar, and the runtime window all showed the generic
Electron icon — the strongest "this is unfinished / untrustworthy" signal at install time.
The first-run wizard and the empty library, while functional, did not yet feel like a
polished product. This change makes the install → launch → first-transcript journey
compelling without touching the credentialed signing path (signing/notarization stays out
of scope, owned by Jim).

## What Changes

- **Branded icon, reproducibly.** Commit the chosen "play + transcript lines" mark as
  `app/build/icon.svg` (the source) and a rendered `app/build/icon.png` (1024×1024). A new
  `app/scripts/build-icons.mjs` renders the SVG → PNG via `rsvg-convert` and asserts the
  output is a real 1024×1024 PNG. Per the v2 icon-pipeline decision, **only `icon.svg` +
  `icon.png` are committed**; electron-builder 26 derives the platform `.icns`/`.ico` from
  `icon.png` at packaging time, so no `.icns`/`.ico` are hand-generated or committed and CI
  needs neither rsvg nor ImageMagick (`icon.png` is committed; `build-icons.mjs` is a
  documented dev step, not a build/CI dependency).
- **electron-builder wiring.** Set `win/mac/linux.icon` to `build/icon.png` explicitly for
  clarity (electron-builder auto-discovers it regardless) and ship `icon.png` via
  extraResources for the runtime window. No NSIS `installerIcon`/`uninstallerIcon` or dmg
  volume icon (those require a committed `.ico`/`.icns`, which the v2 pipeline avoids). No
  signing/notarize fields touched.
- **Runtime window icon.** `main/index.ts` passes `icon:` to `new BrowserWindow`, resolving
  `build/icon.png` for both packaged (`<resources>/icon.png`) and dev (`<app>/build/icon.png`)
  layouts, so the Linux/Windows window + taskbar and every dev run show the mark (macOS uses
  the bundle `.icns`).
- **Polished first-run wizard.** A welcoming hero (mark + "Welcome to Podcast Reader" +
  clearer intro) and labelled hardware / components sections. Presentation only — the
  hardware summary, recommended-pack selection, install-with-progress, and the
  `first_run_complete` gate are unchanged.
- **Branded library empty state.** Replace the bare "Nothing here yet" list with a branded
  empty state: the app mark, a value-prop lead, and a primary "Transcribe your first
  episode" CTA routing to the New view.
- **Theme touch-ups.** Only the styles these two screens need; no global redesign.

No behavior changes to engine APIs, the credential-free renderer, the packaging
`--engine-dir` contract, or the pack-install/first-run logic.

## Impact

- **Code (app/ only):** `app/build/icon.svg` + `icon.png` (assets), `app/scripts/build-icons.mjs`
  (new) + `build-icons` npm script, `app/electron-builder.config.cjs` (icon fields +
  extraResources), `app/src/main/index.ts` (window icon), `app/src/renderer/src/views/setup.ts`
  (hero/sectioning), `app/src/renderer/src/views/library.ts` + new `empty-state.ts` (branded
  empty state), `app/src/renderer/src/style.css` (the two screens' styles).
- **Tests:** `icon-assets.test.ts` (committed-asset + PNG-shape guard), `empty-state.test.ts`
  (CTA targets New), an e2e empty-state CTA-routing assertion; the existing setup-wizard e2e
  heading selector updated to the new welcome copy.
- **Docs:** `app/README.md` icon/branding note; the relevant root `CLAUDE.md` doc rows.

## Capabilities

### Modified Capabilities

- `app-packaging`: ADDED a branded-icon requirement (single committed source → derived
  platform formats → installer/window/dock).
- `app-setup-ui`: ADDED a presentation requirement that the first-run wizard presents a
  polished, welcoming onboarding (the existing behavioral requirement is unchanged).
- `app-views`: ADDED a requirement that the empty library shows a branded first-transcript
  CTA (the existing Library-view requirement is unchanged).
