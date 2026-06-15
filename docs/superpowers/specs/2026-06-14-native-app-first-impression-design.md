# Native App First Impression — Design

**Date:** 2026-06-14
**Status:** Approved brainstorm, pre-systems-review
**Author:** Jim Park, with Claude
**Review history:** v1 — approved brainstorm. Goal: a compelling one-click-install first
impression. Focus chosen: the install → launch → first-transcript journey. Icon direction
chosen: **B (play + transcript lines)**. Code signing / notarization (the only thing
blocking a literally-zero-warning install) is out of scope — credentialed, owned by Jim.
v2 — systems-review of the icon pipeline: electron-builder 26.15.2 bundles `app-builder-bin`,
which generates `.icns`/`.ico` from a single 1024 `build/icon.png` reliably and
cross-platform, and ImageMagick's `.icns` output is unreliable (its `.ico` is fine).
Therefore commit **only `icon.svg` + `icon.png` (1024)** and let electron-builder derive
the platform icons — no hand-generated `.icns`, no rsvg/ImageMagick dependency in CI.

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

### Icon source + build pipeline (v2)
- Commit the chosen mark as `app/build/icon.svg` (1024×1024, the "play + lines" concept on
  the brand-blue squircle) — the reproducible source.
- `app/scripts/build-icons.mjs` (new): renders `icon.svg` → **`build/icon.png` (1024)** via
  `rsvg-convert`, asserting the output exists and is a valid PNG of the right dimensions.
  This is the only generated artifact; it is committed.
- **electron-builder derives the rest:** version 26 (`app-builder-bin`) generates `.icns`
  (macOS) and `.ico` (Windows/NSIS) from `build/icon.png` at packaging time, reliably and
  cross-platform — so we do **not** hand-generate or commit `.icns`/`.ico` (ImageMagick's
  `.icns` is unreliable; avoiding it removes a whole class of malformed-icon risk and keeps
  CI free of rsvg/ImageMagick). `build-icons.mjs` is a documented dev step, not a build/CI
  dependency, because `icon.png` is committed.

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

- **Icon pipeline:** `build-icons.mjs` asserts `build/icon.png` exists, has the PNG magic
  bytes, and is 1024×1024; a committed-asset check confirms `icon.svg` + `icon.png` are
  present (electron-builder generates `.icns`/`.ico` from `icon.png` — not our artifacts to
  validate). `npm run build` stays green with the icon in place.
- **Renderer:** vitest for any new pure logic (empty-state CTA wiring, setup copy/step
  helpers); the existing view tests stay green.
- **e2e:** the first-run and library-empty Playwright paths still pass (and assert the
  empty-state CTA routes to New); no regression in the mock-engine flows.
- **Packaging smoke:** `node scripts/dist.mjs --linux dir` (no signing) produces a build
  that picks up the icon — a non-signing packaging sanity check.

## Risks & mitigations

| Risk | Mitigation |
|------|-----------|
| `.icns`/`.ico` generated off-host are malformed | don't generate them at all — electron-builder 26 derives both from the committed `icon.png` reliably; ImageMagick `.icns` avoided entirely |
| CI lacks rsvg/ImageMagick | CI needs neither — only `icon.png` is committed (rendered locally by the documented `build-icons.mjs`); electron-builder handles the platform formats |
| Window icon path differs packaged vs dev | resolve via app root / `resourcesPath` with a dev fallback, mirroring the engine-resolution pattern |
| Scope creep into a full UI redesign | explicitly first-impression-only; broader uplift is a separate increment |
| First-run polish changing pack-install behavior | presentation-only; the `first_run_complete` gate and pack logic are untouched |

## Follow-ons (tracked, not built here)

1. Full in-app visual uplift across Library/Reader/New/Settings.
2. Installer header/sidebar artwork + dmg background image (richer than icons alone).
3. Signing/notarization for a warning-free install (credentialed — Jim).
