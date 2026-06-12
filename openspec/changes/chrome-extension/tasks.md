# Chrome Extension — Tasks

## 1. Engine: pairing exchange (Python first — the extension builds against a finished API)

- [ ] 1.1 `engine/pairing.py`: in-memory pairing state — mint (6-char unambiguous alphabet, 300 s TTL, replaces prior), claim (constant-time compare, single-use, 5-failed-attempt budget, uniform rejection); unit tests for mint/claim/expiry/budget/replacement; nothing ever written to disk or logs
- [ ] 1.2 `engine/app.py`: `POST /v1/pair` (bearer-authed → `{code, expires_at}`) and `POST /v1/pair/claim` (`{code}` → `{token}`); auth middleware gains the exact-path exemption for `/v1/pair/claim` only; TestClient tests: mint requires auth, claim works without auth header, uniform 403 for wrong/expired/exhausted/absent, single-use, every other route still 401s without a token

## 2. Engine: cookie jars

- [ ] 2.1 `engine/cookies.py`: Netscape jar validation (parse incl. `#HttpOnly_`, domain suffix-match enforcement, size cap) and storage (`<data_dir>/cookies/<domain>.txt`, atomic 0600 via the existing `_secure_write_text` path, dir 0700); list (metadata only) and delete; unit tests incl. foreign-domain rejection and a plaintext-never-in-logs sweep
- [ ] 2.2 `engine/app.py`: `PUT /v1/cookies`, `GET /v1/cookies` (`[{domain, created_at}]`), `DELETE /v1/cookies/{domain}` (404 absent); TestClient tests: validation 400s, metadata-only listing, no jar content in any response
- [ ] 2.3 `engine/process.py`: jar resolution at job dequeue — source host suffix-matched against stored domains; match → `PipelineRequest.cookies` = jar path, else `YT_DLP_COOKIES` env as today; tests for exact-host, subdomain, no-match-falls-back
- [ ] 2.4 `ytdlp.py` + faces: auth-detected download failures raise `download_auth_required` with a neutral message; CLI maps the `YT_DLP_COOKIES` hint (copy unchanged), engine job store maps the extension + file-import hint (per N2 — never the Chrome/Windows cookies-from-browser path); tests for both faces' hint mapping
- [ ] 2.5 Diagnostic/log sweep: assert jar content and pairing codes excluded from engine logs and any diagnostic surface; Python gates (pytest, mypy strict, ruff check+format)

## 3. App: Settings sections + IPC

- [ ] 3.1 Main + preload: IPC for `pairStart` (mints via `EngineClient`), `cookiesList`, `cookiesDelete`; typed bridge additions; new shapes (`PairStartResponse`, `CookieJarInfo`) appended to `app/src/shared/types.ts` with comment pins (the one mirror both TS consumers import — Q4 decision)
- [ ] 3.2 Settings view: "Connect browser extension" section (port + code + combined `<port>-<code>` string + expiry countdown, re-mint button) and "Cookies" section (domain list with capture date, per-domain delete); vitest unit for the section logic
- [ ] 3.3 Mock engine (`app/tests/mock-engine/server.ts`): `/v1/pair`, `/v1/pair/claim` (scriptable code, exemption from the mock's auth check), `/v1/cookies` routes + `__mock` controls; existing app e2e suite still green

## 4. Extension scaffold

- [ ] 4.1 `extension/` workspace: `package.json` (exact-pinned vite, typescript, vitest; eslint config consistent with `app/`), strict tsconfig including `../app/src/shared/types.ts`, vite MV3 build (service worker + popup entries), `npm run build` produces a loadable unpacked dir and a deterministic zip
- [ ] 4.2 `manifest.json`: MV3; `permissions` exactly `storage, alarms, notifications, contextMenus, activeTab`; `host_permissions` exactly `http://127.0.0.1/*`; `optional_permissions: cookies` + `optional_host_permissions` (`https://*/*`, `http://*/*`); no content scripts; `minimum_chrome_version` 120 (alarms 30 s floor); action popup + icons
- [ ] 4.3 Typed engine client for the extension (claim, health, jobs, events fetch-stream, cookies PUT) + `chrome.storage.local` wrapper (pairing `{port, token}`, bounded tracked-job list); vitest units (mocked fetch/chrome)

## 5. Extension pairing

- [ ] 5.1 Popup pairing flow: port+code form (accepts the combined paste string), claim → authed health verify → store; failure leaves prior pairing untouched with retry; vitest units
- [ ] 5.2 Connection states: health probe on popup open — connected / app-not-running (launch affordance) / 401 → re-pair flow replacing stored pairing; vitest units

## 6. Extension jobs

- [ ] 6.1 Toolbar action (activeTab URL) + context-menu item (`info.pageUrl`) → `POST /v1/jobs {requires_confirmation: false}`; track id in storage; vitest units for URL selection and submission paths
- [ ] 6.2 Popup progress: hydrate tracked jobs from records on every open, then attach fetch+ReadableStream `/v1/events` (header auth) for live steps; stream scoped to popup lifetime; failed jobs render `{code, message, hint}`; vitest units for the hydrate-then-stream merge
- [ ] 6.3 Service worker: stateless alarm loop (30 s) while tracked jobs are non-terminal → poll records → `chrome.notifications` on terminal → clear alarm when idle; storage-driven across SW restarts; vitest units for the scheduling decisions
- [ ] 6.4 Engine-unreachable fallback: offer `podcast-reader://transcribe?url=<page>` (lands confirm-gated in the app per the existing protocol path); no silent extension-side queuing

## 7. Extension cookie capture

- [ ] 7.1 Netscape serializer from `chrome.cookies.Cookie[]` (7-field lines, `#HttpOnly_` prefix, expiry handling); vitest units against fixture cookies
- [ ] 7.2 Capture flow in the popup: visible only for `download_auth_required` failures; `chrome.permissions.request` scoped to the failed domain (+ `cookies`); decline = clean no-op; grant → getAll → serialize → `PUT /v1/cookies` → one-click resubmission; no cookie content in storage or logs; vitest units with mocked chrome APIs

## 8. E2e & CI

- [ ] 8.1 Playwright extension harness: `chromium.launchPersistentContext` with `--load-extension` against the mock engine (reusing the app's fixture pattern for `PODCAST_READER_DATA_DIR`-less direct port/token scripting); specs: pairing happy path + wrong-code rejection; submit-from-popup → progress; popup close/reopen hydration; cookie push with pre-granted permission (the optional-permission prompt itself is not automatable — documented as a manual check)
- [ ] 8.2 Real-engine pairing round-trip: extend the app's integration smoke (or a pytest integration test) with mint → claim → authed health using the real engine, bracketing the mock's pairing fidelity
- [ ] 8.3 CI: `extension` job in `ci.yml` — npm ci, `tsc --noEmit`, eslint, vitest, vite build, Playwright e2e under xvfb; cache npm + browsers
- [ ] 8.4 Full gates: pytest, mypy strict, ruff, app `tsc`/vitest/e2e, extension `tsc`/vitest/e2e; `openspec validate chrome-extension`

## 9. Docs, packaging & wrap-up

- [ ] 9.1 README: extension section (load-unpacked install, pairing walkthrough, cookie-sharing explanation incl. the honest retention note); CLAUDE.md: `extension/` rows, new engine endpoints (`pair`, `pair/claim`, `cookies`), extension commands; parent-design hint copy now extension-aware (N2)
- [ ] 9.2 Store packaging: `npm run dist` zip suitable for Chrome Web Store upload; listing assets checklist documented
- [ ] 9.3 [USER-BLOCKING] Chrome Web Store developer account: registration ($5 fee, Google account) and publication (listing, privacy disclosures for the `cookies` optional permission, review cycle) — cannot proceed without the user; until then, install is load-unpacked from a release zip
- [ ] 9.4 Systems-review of the implementation diff; PR referencing this change
