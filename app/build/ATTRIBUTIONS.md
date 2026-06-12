# Third-party attributions

Podcast Reader (desktop app) is © 2026 Jim Park, MIT licensed
(see `LICENSE.podcast-reader.txt` in this directory).

Installed builds bundle the following third-party components:

| Component | License | Source |
|-----------|---------|--------|
| Electron (incl. Chromium and Node.js) | MIT (Chromium: BSD-3-Clause and others) | https://github.com/electron/electron |
| electron-updater | MIT | https://github.com/electron-userland/electron-builder |

Chromium and Node.js carry their own bundled third-party notices; see
`LICENSES.chromium.html` shipped alongside the Electron binaries.

The packaged engine under `resources/engine/` (when present) is a frozen
Python application; its bundled dependency licenses (faster-whisper, FastAPI,
uvicorn, CTranslate2, and transitive packages) are collected as part of the
engine freeze in Phase 4.
