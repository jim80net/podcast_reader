# app-packaging

## ADDED Requirements

### Requirement: Branded app icon across surfaces
The app SHALL ship a branded icon — the "play + transcript lines" mark — across the
installer, the macOS dock/.app, the Windows taskbar, and the runtime window, derived from a
single committed source. The icon source SHALL be committed as a 1024×1024 SVG
(`build/icon.svg`) and a rendered 1024×1024 PNG (`build/icon.png`); a documented dev script
(`scripts/build-icons.mjs`, `npm run build-icons`) SHALL render the SVG to the PNG and assert
the PNG is a valid 1024×1024 image. electron-builder SHALL derive the platform `.icns`
(macOS) and `.ico` (Windows/NSIS) from `build/icon.png` at packaging time; the build
SHALL NOT require committing or hand-generating `.icns`/`.ico`, and a fresh checkout and CI
SHALL build the installer without `rsvg-convert` or ImageMagick. The signing/notarization
configuration SHALL be unaffected.

#### Scenario: Single committed source derives the platform icons
- **WHEN** the app is packaged with electron-builder from a fresh checkout
- **THEN** the installer and the bundled app carry the branded icon, derived from the
  committed `build/icon.png`, with no `.icns`/`.ico` committed and no rsvg/ImageMagick on the
  build host

#### Scenario: The runtime window shows the mark in dev and packaged runs
- **WHEN** the app window is created
- **THEN** the window is given the branded icon, resolved from the packaged
  `<resources>/icon.png` when packaged and from `<app>/build/icon.png` in dev

#### Scenario: Re-rendering the source reproduces the committed PNG
- **WHEN** `npm run build-icons` is run against the committed `build/icon.svg`
- **THEN** it produces a valid 1024×1024 PNG at `build/icon.png` and fails loudly if the
  output is not a 1024×1024 PNG
