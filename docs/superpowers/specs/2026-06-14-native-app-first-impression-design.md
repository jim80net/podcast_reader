# Native App First Impression — Design

**Date:** 2026-06-14
**Status:** Approved brainstorm, pre-systems-review
**Author:** Jim Park, with Claude
**Review history:** v1 — approved brainstorm. Goal: a compelling one-click-install first
impression. Focus chosen: the install → launch → first-transcript journey. Icon direction
chosen: **B (play + transcript lines)**. Code signing / notarization (the only thing
blocking a literally-zero-warning install) is out of scope — credentialed, owned by Jim.

## Problem

The packaged desktop app has no visual identity: `build/` contains no icon, so the
installer, dock, taskbar, and window all show the generic Electron icon — the strongest
"this is unfinished / untrustworthy" signal at install time. The installer is already
one-click-configured (NSIS `oneClick`, dmg) but unbranded, and the first-run wizard and the
empty library, while functional, don't yet feel like a polished product. This change makes
the first impression compelling without touching the credentialed signing path.

## Goals

- A real, reproducible **app + installer icon** (the chosen "play + lines" mark) across
  every surface: installer, macOS dock/.app, Windows taskbar, and the dev/runtime window.
- **Installer branding** where electron-builder allows it without signing (NSIS installer
  icons; dmg volume icon).
- A **polished first-run wizard** and a **welcoming library empty state** — the first two
  screens a new user sees.
- Reproducibility: the icon is generated from a committed **SVG source** via a script, not
  hand-placed binaries — so it can be regenerated or swapped (a designer's 1024px PNG drops
  in).
- Preserve all existing invariants: the credential-free renderer, the packaging contract
  (`--engine-dir` extraResources), and a green build/e2e.

## Non-goals (out of scope)

- Code signing / notarization (credentialed — Jim).
- A full in-app visual uplift across all views (a separate, later increment — this one is
  the *first-impression* path only).
- New brand typography / full design system.

## Components

### Icon source + build pipeline
- Commit the chosen mark as `app/build/icon.svg` (1024×1024, the "play + lines" concept on
  the brand-blue squircle).
- `app/scripts/build-icons.mjs` (new): renders the SVG to the icon set with the tooling on
  hand (`rsvg-convert` for SVG→PNG; ImageMagick for `.ico`/`.icns` assembly), producing:
  - `build/icon.png` (1024 — Linux + the canonical source electron-builder can derive from),
  - `build/icon.ico` (multi-size 16–256 — Windows / NSIS),
  - `build/icon.icns` (macOS).
  The script is idempotent and validated (asserts each output exists and is a non-trivial,
  correctly-typed file). It runs as a `predist`/documented step, not on every build, since
  the rendered binaries are committed.
- **Decision (for systems-review to confirm):** commit the *generated* `icon.png/ico/icns`
  (so CI packaging needs no rsvg/IM), with the SVG + script as the reproducible source —
  rather than generating during the build (which would add native deps to CI).

### electron-builder wiring (`electron-builder.config.cjs`)
- Point mac/win/linux at the generated icons (electron-builder auto-discovers `build/icon.*`
  but set them explicitly for clarity). Add NSIS `installerIcon`/`uninstallerIcon` and the
  dmg icon. No signing fields touched.

### Runtime window icon (`main/index.ts`)
- Pass `icon: <png>` to `new BrowserWindow` so Linux/Windows window + taskbar show the mark
  in dev and unpackaged runs (macOS uses the bundle icns). Resolve the path for both
  packaged (`process.resourcesPath`/app root) and dev layouts.

### First-run wizard polish (`renderer/src/views/setup.ts`)
- A clearer welcome/intro, sectioning, and progress affordances; keep the existing
  hardware-summary + recommended-pack flow and the `first_run_complete` gate intact —
  this is presentation polish, not a behavior change to the pack install logic.

### Library empty state (`renderer/src/views/library.ts`)
- Replace the bare empty list with a branded, welcoming empty state (icon/mark, one-line
  value prop, a primary "Transcribe your first episode" CTA → the New view).

### Theme touch-ups (`renderer/src/style.css`)
- Only what these two screens need (spacing, the empty-state + setup styles); no global
  redesign.

## Testing

- **Icon pipeline:** a Node test (or the build-icons script's own asserts) verifies the
  generated `icon.png/ico/icns` exist with valid magic bytes and the expected sizes;
  `npm run build` stays green with the icons in place.
- **Renderer:** vitest for any new pure logic (empty-state CTA wiring, setup copy/step
  helpers); the existing view tests stay green.
- **e2e:** the first-run and library-empty Playwright paths still pass (and assert the
  empty-state CTA routes to New); no regression in the mock-engine flows.
- **Packaging smoke:** `node scripts/dist.mjs --linux dir` (no signing) produces a build
  that picks up the icon — a non-signing packaging sanity check.

## Risks & mitigations

| Risk | Mitigation |
|------|-----------|
| `.icns`/`.ico` generated off-host are malformed | validate magic bytes + sizes in the build-icons asserts; commit generated files so CI doesn't depend on rsvg/IM |
| CI lacks rsvg/ImageMagick | don't generate in CI — commit the binaries; SVG+script are the reproducible source |
| Window icon path differs packaged vs dev | resolve via app root / `resourcesPath` with a dev fallback, mirroring the engine-resolution pattern |
| Scope creep into a full UI redesign | explicitly first-impression-only; broader uplift is a separate increment |
| First-run polish changing pack-install behavior | presentation-only; the `first_run_complete` gate and pack logic are untouched |

## Follow-ons (tracked, not built here)

1. Full in-app visual uplift across Library/Reader/New/Settings.
2. Installer header/sidebar artwork + dmg background image (richer than icons alone).
3. Signing/notarization for a warning-free install (credentialed — Jim).
