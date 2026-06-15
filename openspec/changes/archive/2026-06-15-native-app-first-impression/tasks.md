# Tasks

## 1. Icon source + build pipeline
- [x] 1.1 Commit `app/build/icon.svg` (play + transcript-lines mark) and rendered `icon.png` (1024)
- [x] 1.2 Add `app/scripts/build-icons.mjs` (rsvg-convert render + PNG magic/dimension asserts)
- [x] 1.3 Add the `build-icons` npm script (dev step, not wired into build/CI)
- [x] 1.4 Test: committed `icon.svg` + `icon.png` exist; `icon.png` has PNG magic + 1024×1024

## 2. electron-builder wiring
- [x] 2.1 Set `win/mac/linux.icon` to `build/icon.png` explicitly (no signing/notarize touched)
- [x] 2.2 Ship `build/icon.png` via extraResources for the runtime window
- [x] 2.3 No NSIS installer/uninstaller icon, no dmg volume icon (would require committed `.ico`/`.icns`)

## 3. Runtime window icon
- [x] 3.1 `main/index.ts`: pass `icon:` to `new BrowserWindow`, resolving packaged vs dev paths
- [x] 3.2 contextIsolation/sandbox/nodeIntegration unchanged

## 4. First-run wizard polish (presentation only)
- [x] 4.1 Welcome hero (mark + heading + clearer intro) and labelled hardware/components sections
- [x] 4.2 Hardware summary, recommended-pack selection, install-with-progress, first-run gate unchanged
- [x] 4.3 Update the setup-wizard e2e heading selector to the new welcome copy

## 5. Library empty state
- [x] 5.1 Pure `empty-state.ts` (branded copy + CTA href → New)
- [x] 5.2 Render the branded empty state via `el()` (mark, value prop, primary CTA)
- [x] 5.3 Vitest: empty-state copy + CTA targets the New route
- [x] 5.4 e2e: empty library shows the CTA and it routes to New

## 6. Theme touch-ups
- [x] 6.1 Empty-state + setup styles in `style.css` (no global redesign)

## 7. OpenSpec change
- [x] 7.1 `.openspec.yaml`, proposal, design, tasks
- [x] 7.2 Spec deltas: app-packaging, app-setup-ui, app-views (ADDED requirements)
- [x] 7.3 `openspec validate native-app-first-impression --strict` passes

## 8. Docs
- [x] 8.1 `app/README.md` icon/branding note
- [x] 8.2 Root `CLAUDE.md` doc rows

## 9. Verification
- [x] 9.1 `npm run typecheck && npm run lint && npm run test` green
- [x] 9.2 `npm run build` green; `build-icons` reproduces the committed PNG
- [x] 9.3 e2e (views + packs) green under xvfb
