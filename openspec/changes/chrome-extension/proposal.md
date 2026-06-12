# Chrome Extension (Desktop Phase 5)

## Why

Phases 1–4 delivered the engine, multi-provider chapters, the Electron app, and the download manager — but the parent design's v1 input UX promises *two* faces: the app window **plus** "a thin Chrome extension that sends the current tab". The extension is also load-bearing for the novice auth path: `--cookies-from-browser chrome` is permanently broken on Windows (Chrome 127 App-Bound Encryption, yt-dlp #10927), so extension-assisted cookie capture (parent design F2) is the only one-click way an ordinary user can transcribe login-gated content. Phase 5 is the final phase of `docs/superpowers/specs/2026-06-11-desktop-packaging-design.md` (v3).

## What Changes

- New `extension/` workspace: a Chrome MV3 extension — a toolbar action that opens the popup whose submit affordance sends the current tab to the engine (with `default_popup` set, `action.onClicked` never fires — per U1), plus a context-menu item, popup with live progress (fetch + ReadableStream with header auth, job-record hydration on every popup open), background completion notifications via `chrome.alarms` polling (no long-lived SSE in the MV3 service worker — terminated at ~30 s idle; the Chrome 116 lifetime extension covers WebSockets, not SSE).
- Pairing per the parent design's N1 flow: the app's Settings view displays the fixed per-install port and a short-lived 6-character code; the user enters them in the extension popup, which exchanges the code for the bearer token and stores `{port, token}`. New engine endpoints back this: `POST /v1/pair` (bearer-authed, app-only: mints the code) and `POST /v1/pair/claim` (the engine's **single unauthenticated route**: single-use, TTL- and attempt-capped code-for-token exchange).
- Extension-assisted cookie capture (F2): on an auth-required download failure, the popup offers to share the user's login for that domain — `cookies` optional permission plus a domain-scoped origin permission requested on demand, `chrome.cookies.getAll`, Netscape-format jar `PUT` to a new engine cookie endpoint. The engine persists jars per domain (0600, `<data_dir>/cookies/`) because yt-dlp consumes a `--cookies` *file*; jars are listable/deletable as metadata only and are never readable back, logged, or included in diagnostics. The engine job runner prefers a matching jar over the `YT_DLP_COOKIES` env fallback.
- Phase-aware auth-failure hints (parent design N2): download auth failures gain a distinct error code, and engine-face hints now reference extension cookie sharing (plus file import); CLI hints keep `YT_DLP_COOKIES`.
- App additions: Settings gains an "Extension pairing" section (mint + display code) and a "Cookies" management section (list/delete captured domains).
- Submission semantics adjudicated: extension popup/context-menu submissions use `requires_confirmation: false` — a deliberate click inside a user-installed, token-authed surface *is* the confirmation, exactly like the app's New view. The confirm gate exists for the **unauthenticated** `podcast-reader://` channel, which any web page can fire; the extension's engine-unreachable fallback routes through that protocol and therefore stays confirm-gated.
- Testing: vitest for extension logic; Playwright (chromium persistent context, `--load-extension`) against the reused `app/tests/mock-engine`, which gains the pairing/cookie routes. CI gains an `extension` job.

No breaking changes: engine API grows additively; app and CLI behavior unchanged except the new Settings sections and hint copy.

## Capabilities

### New Capabilities

- `ext-pairing`: user-mediated code exchange, token storage and confinement (threat model: `chrome.storage.local` ≈ same-user 0600 file), least-privilege manifest, reconnection and re-pair.
- `ext-jobs`: submit current tab (popup submit affordance + context menu, confirmation-free by adjudication; per U1 the toolbar click opens the popup), popup progress via fetch-stream with hydration, `chrome.alarms` completion notifications, protocol fallback when the engine is down.
- `ext-cookie-capture`: on-demand optional-permission flow, domain-scoped capture, Netscape jar push, no extension-side retention.
- `cookie-management`: engine cookie-jar endpoints and file lifecycle (0600 storage, metadata-only listing, per-domain delete), jar-aware download step, phase-aware auth-failure hints.

### Modified Capabilities

- `engine-service`: the bearer-auth-everywhere requirement gains its single documented exception (`POST /v1/pair/claim`, exempted by method + path per U5); new pairing-code exchange requirement (mint + claim, in-memory, single-use, capped, page-origin-hardened per U3).
- `app-views`: Settings view gains extension-pairing and cookie-management sections.
- `tools-seeding`: the extraction-failure self-update retry stays scoped to `download_failed`; the new `download_auth_required` code surfaces immediately — an update can't conjure cookies (per U2).

## Impact

- **Code:** new `extension/` (TypeScript, vite MV3 build, no framework — mirrors the app renderer's stance); engine edits in `engine/app.py` (pair/claim/cookies routes, auth-middleware exemption), new small `engine/pairing.py` + `engine/cookies.py`, `engine/process.py` (jar resolution at job dequeue), `ytdlp.py` (distinct auth-failure code); app edits in Settings view + IPC for pair/cookies.
- **Types:** the extension becomes the second TS consumer of the engine shapes — the Q4 revisit trigger. Decision (see design): both consumers import the one comment-pinned mirror `app/src/shared/types.ts`; no codegen, no shared package yet.
- **Tests:** pytest for pairing/cookies/hints; extension vitest unit; Playwright extension e2e against the extended mock engine.
- **CI:** new `extension` job (typecheck, lint, vitest, build, Playwright e2e under xvfb).
- **Docs:** README extension section (install unpacked, pairing walkthrough), CLAUDE.md rows.
- **Deps:** `extension/` dev-side npm only (vite, typescript, vitest; playwright reused); zero new Python deps.
- **Out of scope:** Firefox/Safari ports, Chrome Web Store publication (developer account is [USER-BLOCKING]; dev installs load unpacked), hosted inference (issue #10), service-worker-resident progress streaming.
