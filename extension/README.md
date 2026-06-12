# Podcast Reader — Chrome extension

Manifest V3 extension for the [podcast_reader](../README.md) desktop app:
submit the current tab for transcription, watch live progress in the popup,
get a notification on completion, and share a site login (cookie jar) when a
source needs authentication. Everything rides the engine's authenticated
localhost `/v1` API; the user-mediated pairing flow (port + one-time code
from the desktop app's Settings) is how the extension obtains the bearer
token. See the repo README's "Chrome extension" section for the user-facing
walkthrough, and `openspec/changes/chrome-extension/design.md` for the
design record.

## Layout

| Path | Purpose |
|------|---------|
| `public/manifest.json` | MV3 manifest: least-privilege permissions, `optional_permissions: cookies`, no content scripts |
| `src/popup.ts` + `popup.html`/`popup.css` | The popup: pairing form, submit affordance, live progress (hydrate-then-stream), cookie capture |
| `src/sw.ts` | Service worker: context-menu submit + stateless 30 s alarm poll → notifications/badge |
| `src/client.ts` | Typed engine client (claim, health, jobs, events stream, cookies PUT) |
| `src/pairing.ts`, `src/connection.ts` | Pairing input parsing + claim flow; popup-open connection probe |
| `src/etld.ts`, `src/capture.ts`, `src/netscape.ts` | Registrable-domain derivation, capture targeting, Netscape jar serialization |
| `src/storage.ts`, `src/tracking.ts`, `src/jobs-view.ts` | `chrome.storage.local` wrapper, tracked-job list, pure presentation/poll logic |
| `tests/e2e/` | Playwright suites: real built extension + the app's mock engine |
| `scripts/zip.mjs` | Deterministic `podcast-reader-extension.zip` from `dist/` |

Shared API types import from `../app/src/shared/types.ts` — the single
comment-pinned mirror both TS consumers use (its key-set parity against the
real engine is asserted by `app/tests/e2e/integration.spec.ts`).

## Development

Requires Node >= 24 (the e2e mock engine runs TypeScript via native type
stripping).

```bash
npm install
npm run typecheck    # tsc --noEmit (src + tests + configs)
npm run lint         # eslint (includes the textContent-only DOM fence)
npm run test         # vitest unit tests
npm run build        # vite MV3 build → dist/ + deterministic zip
npm run dist         # alias of build: dist/ + podcast-reader-extension.zip
```

There is no dev-server mode: MV3 service workers and popups load from disk,
so the loop is `npm run build` → reload the extension (`chrome://extensions`
→ the refresh icon on the card). The popup can be inspected like any page
(right-click the toolbar icon → Inspect popup, or open
`chrome-extension://<id>/popup.html` in a tab).

### Load unpacked

1. `npm run build`
2. Open `chrome://extensions`, enable **Developer mode**
3. **Load unpacked** → select `extension/dist`

### E2e (Playwright)

```bash
npm run build                  # the suite loads the real dist/
npm run e2e                    # xvfb-run -a npm run e2e on headless hosts
```

The harness (`tests/e2e/fixtures.ts`) spawns the app's scriptable mock
engine (`../app/tests/mock-engine/server.ts`) and launches a persistent
Chromium context with `--load-extension` — extensions need a headed
Chromium, hence xvfb in CI (the `extension` job in
`.github/workflows/ci.yml`). The popup is driven as a tab
(`chrome-extension://<id>/popup.html`); Chrome offers no automatable path to
the real toolbar popup window, and the page is identical either way.

### Manual check: the optional-permission prompt

Chrome renders the `chrome.permissions.request` prompt outside any
automatable surface, so the cookie-capture e2e pre-grants by stubbing the
API (the grant/decline branches are unit-tested in `src/capture.test.ts`).
After changes touching capture, verify the real prompt once by hand:

1. Pair with a running desktop app, then submit a members-only source so a
   job fails with `download_auth_required` (any logged-out paywalled video
   URL works).
2. Click "Share your `<domain>` login" in the popup and confirm Chrome's
   prompt names the `cookies` permission and **only that site's** origins.
3. Decline → confirm nothing happens and the affordance remains.
4. Click again, accept → confirm "Login shared." and the retry affordance,
   and that the domain appears in the desktop app's Settings → Cookies.

## Store packaging (publication pending)

`npm run dist` produces `podcast-reader-extension.zip` — entries sorted,
stored uncompressed, fixed timestamps, so identical build bytes give an
identical archive. That zip is the Chrome Web Store upload artifact;
publication itself is blocked on a developer account (see
`openspec/changes/chrome-extension/tasks.md`, task 9.3). Until then,
install is load-unpacked from the zip or a local build.

Listing assets checklist (prepare alongside the account):

- [ ] Store icon 128×128 PNG (`public/icons/icon128.png` is the source)
- [ ] At least one 1280×800 (or 640×400) screenshot — popup with a running
      job is the natural shot
- [ ] Short description (≤132 chars) and detailed description
- [ ] Category (Productivity) and language
- [ ] Privacy disclosures: single purpose statement; justification for each
      permission — notably the optional `cookies` permission + broad
      optional host patterns (requested per-site at capture time, cookie
      data sent only to the user's own localhost engine, never retained by
      the extension)
- [ ] Privacy policy URL (required once the `cookies` permission is
      declared)
