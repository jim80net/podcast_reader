# Native App First Impression — Design

The authoritative design (problem, goals, non-goals, components, testing, risks, follow-ons)
lives in `docs/superpowers/specs/2026-06-14-native-app-first-impression-design.md` (design
**v2**). This file records the design decisions that bind the implementation; defer to the
full doc for rationale.

## Icon pipeline (v2 decision — authoritative)

electron-builder 26.15.2 bundles `app-builder-bin`, which generates `.icns` (macOS) and
`.ico` (Windows/NSIS) from a single 1024×1024 `build/icon.png` reliably and cross-platform.
ImageMagick's `.icns` output is unreliable. Therefore:

- **Commit only `build/icon.svg` (source) + `build/icon.png` (1024, rendered).** No
  hand-generated or committed `.icns`/`.ico` — electron-builder derives them at packaging
  time.
- **`scripts/build-icons.mjs` is a documented DEV step, not a build/CI dependency.** It
  renders `icon.svg` → `icon.png` via `rsvg-convert` and asserts the PNG magic bytes +
  1024×1024 dimensions. Because `icon.png` is committed, a fresh checkout and CI never run
  it and never need rsvg/ImageMagick.
- **No NSIS `installerIcon`/`uninstallerIcon`, no dmg volume icon.** Those config fields
  require a committed `.ico`/`.icns`, which this pipeline deliberately avoids; electron-builder
  uses the derived app icon for the installer.

## electron-builder wiring

`win/mac/linux.icon` are set explicitly to `build/icon.png` for intent clarity, though
electron-builder auto-discovers `build/icon.*` regardless. The runtime window needs the file
at run time, so `build/icon.png` is also shipped via extraResources to `<resources>/icon.png`.
No signing/notarize fields are touched.

## Runtime window icon

`new BrowserWindow({ icon })` resolves the mark for both layouts, mirroring the
packaged-vs-dev resolution used elsewhere in `main/index.ts`:

- **Packaged:** `join(process.resourcesPath, 'icon.png')` (the extraResources copy).
- **Dev/unpackaged:** `join(app.getAppPath(), 'build', 'icon.png')` (getAppPath() is the
  `app/` dir).

macOS draws the dock/window from the bundle `.icns` and ignores this, so it mainly brands
the Linux/Windows window + taskbar and every dev run. contextIsolation/sandbox/nodeIntegration
are unchanged.

## Renderer polish (presentation only)

- **Setup wizard:** a hero (mark + welcome heading + clearer intro) and labelled hardware /
  components `<section>`s. The hardware summary, recommended-pack selection,
  install-with-progress, and the `first_run_complete` gate are untouched. The setup-wizard
  e2e heading selector is updated to the new welcome copy.
- **Library empty state:** a pure `empty-state.ts` provides the branded copy and — load
  bearingly — the CTA's New-view href, unit-tested without a DOM; `views/library.ts` renders
  it via `el()`/textContent (no innerHTML). The non-empty library is unchanged.

All DOM is built via `dom.ts` `el()` (textContent only); the eslint innerHTML fence holds.
