/**
 * electron-builder configuration (design decisions 9, 10).
 *
 * - Engine payload: a frozen onedir passed via the `--engine-dir` build input
 *   (`scripts/dist.mjs` sets PODCAST_READER_ENGINE_DIR) is copied UNCOMPRESSED
 *   into `<resources>/engine/` as extraResources — executables cannot run from
 *   inside the asar archive, and the spike layout (engine executable, sibling
 *   whisper-worker, shared _internal/) maps onto it directly. Builds without
 *   an engine dir are valid: the app falls back to the app-shell spawn chain
 *   (PODCAST_READER_ENGINE_CMD, then `uv run podcast-reader serve`).
 * - Updates: full-download against GitHub Releases (decision 9 — app and
 *   engine version in lockstep while the product is young, so differential
 *   transfers would degrade to ~full size; revisit when shell and engine
 *   release cadences decouple). The extraResources layout keeps the
 *   blockmap-friendly differential path open as a config change.
 * - Signing/notarization (decision 10): NOT wired — user-blocking
 *   prerequisites (tasks 6.4/6.5). When credentials exist, add
 *   `win.signtoolOptions` / `mac.notarize` here and enable the tag pipeline
 *   (.github/workflows/release.yml).
 */
const { existsSync } = require('node:fs')
const { join, resolve } = require('node:path')

const extraResources = [
  { from: '../LICENSE', to: 'LICENSE.podcast-reader.txt' },
  { from: 'build/ATTRIBUTIONS.md', to: 'ATTRIBUTIONS.md' },
  // The runtime BrowserWindow icon (main/index.ts) loads this at
  // `<resources>/icon.png` packaged; macOS uses the bundle .icns instead, but
  // shipping it once covers the Linux/Windows window + taskbar mark uniformly.
  { from: 'build/icon.png', to: 'icon.png' }
]

const engineDir = process.env.PODCAST_READER_ENGINE_DIR
if (engineDir !== undefined && engineDir !== '') {
  const dir = resolve(engineDir)
  if (!existsSync(dir)) {
    throw new Error(`engine dir does not exist: ${dir}`)
  }
  const expected = ['podcast-reader-engine', 'podcast-reader-engine.exe']
  if (!expected.some((name) => existsSync(join(dir, name)))) {
    throw new Error(
      `engine dir lacks an engine executable (${expected.join(' or ')}): ${dir}`
    )
  }
  extraResources.push({ from: dir, to: 'engine' })
}

module.exports = {
  appId: 'com.jim80net.podcast-reader',
  productName: 'Podcast Reader',
  artifactName: '${name}-${version}-${os}-${arch}.${ext}',
  directories: { output: 'dist', buildResources: 'build' },
  files: ['out/**'],
  asar: true,
  extraResources,
  // Installer-level protocol registration (design decision 7): NSIS registry
  // keys on Windows, CFBundleURLTypes on macOS.
  protocols: [{ name: 'Podcast Reader', schemes: ['podcast-reader'] }],
  // Branding icon (native-app-first-impression, v2 icon pipeline): the single
  // committed master `build/icon.png` (rendered from `build/icon.svg` by
  // `npm run build-icons`) is what electron-builder 26 derives every platform
  // format from — `.icns` (macOS .app/dmg), `.ico` (Windows/NSIS) — at
  // packaging time. electron-builder auto-discovers `build/icon.*`; the
  // per-platform `icon` fields below just make that intent explicit. NSIS
  // `installerIcon`/`uninstallerIcon` and the dmg volume icon are deliberately
  // NOT set: they require a committed `.ico`/`.icns`, which the v2 pipeline
  // avoids (electron-builder uses the derived app icon for the installer).
  win: {
    icon: 'build/icon.png',
    target: [{ target: 'nsis', arch: ['x64'] }]
  },
  nsis: {
    // Per-user (decision 9): no elevation prompt, smoother auto-updates.
    oneClick: true,
    perMachine: false,
    deleteAppDataOnUninstall: false,
    license: '../LICENSE'
  },
  mac: {
    icon: 'build/icon.png',
    // dmg for first install; zip is the target electron-updater requires.
    target: [{ target: 'dmg' }, { target: 'zip' }],
    category: 'public.app-category.productivity'
  },
  linux: {
    icon: 'build/icon.png',
    // Not a ship target — `--linux dir` exists so the packaging pipeline is
    // provable on Linux dev hosts and CI without a Windows/macOS runner.
    target: [{ target: 'dir' }]
  },
  publish: { provider: 'github', owner: 'jim80net', repo: 'podcast_reader' },
  npmRebuild: false
}
