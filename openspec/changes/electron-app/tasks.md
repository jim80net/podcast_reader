# Electron MVP — Tasks

## 1. Engine additions (Python first — the app builds against a finished API)

- [x] 1.1 `engine/jobs.py`: `submit(source, title, *, requires_confirmation=False)` journals awaiting-confirmation without enqueueing; `confirm(job_id)` (awaiting-confirmation → queued + enqueue, `KeyError`/state error otherwise); `discard(job_id)` (awaiting-confirmation only); `JobStore.shutdown()` sets a stopping flag and a job that fails while stopping is journaled `interrupted`, not `failed` (per P2); recovery test: awaiting-confirmation survives restart un-enqueued; unit tests for all transitions incl. 409-shaped state errors and the stopping-flag interruption path (per P2)
- [x] 1.2 `engine/app.py`: `JobSubmission.requires_confirmation: bool = False`; `POST /v1/jobs/{id}/confirm` and `DELETE /v1/jobs/{id}` routes (404 unknown, 409 wrong state); TestClient tests incl. default-submission-unchanged regression
- [x] 1.3 `engine/app.py` + `engine/process.py`: `POST /v1/shutdown` → 202 then `server.should_exit = True` (server handle injected by `serve_engine`); `serve_engine` sets uvicorn `timeout_graceful_shutdown=3` so engine exit is bounded regardless of supervisor (per P1); auth-rejection test; lifecycle test asserting children reaped + discovery file removed after endpoint-triggered exit (extend the existing serve_engine lifecycle test) — extended: trigger shutdown WITH a live SSE subscriber attached, assert bounded exit and that the `finally` cleanup ran (discovery file removed) (per P1); assert a job failing during shutdown is journaled `interrupted`, not `failed` (per P2)
- [x] 1.4 `chapters.py`/`engine/app.py`: `POST /v1/keys/test {provider, api_key?}` — key resolution order supplied > pushed > env; minimal completion via the Phase 2 transport (small max_tokens); `{ok, detail}` with K4 redaction (mocked-401-echoing-key test: key and body absent from response and logs); 400 on unknown provider with no outbound call; tested key not stored; `provider=custom` resolves `custom_provider_url` from current settings, 400 if empty/invalid (per P9)
- [x] 1.5 `engine/app.py`: `GET /v1/providers` — registry ids + default model + key-available boolean (pushed-or-env, boolean only); response never contains key material; TestClient tests: list includes exactly the six registry ids, response contains no key material with keys pushed and env vars set (per P4)
- [x] 1.6 Python gates: pytest unit, mypy strict, ruff check+format

## 2. App scaffold & engine client

- [x] 2.1 `app/` workspace: `package.json` (exact-pinned electron, electron-vite, electron-builder, electron-updater, typescript, vitest, playwright), strict `tsconfig`, electron-vite config, `src/{main,preload,renderer,shared}/` skeleton; `npm run dev` boots a window
- [x] 2.2 `src/shared/types.ts`: TS mirrors of `JobRecord`, `PipelineEvent`, `LibraryEntry`, `EngineSettings`, `DiscoveryInfo`, job/event literals — each with a comment pinning it to `podcast_reader/types.py` / `engine/process.py`
- [x] 2.3 `src/main/engine-client.ts`: typed authed client for the full `/v1` surface; SSE via fetch+ReadableStream with header auth, reconnect backoff, hydrate-from-`GET /v1/jobs` on every (re)connect; unit tests (vitest, mocked fetch)
- [x] 2.4 `src/main/engine.ts` supervision: `data_dir()` mirror (env override, `~/PodcastReader`); adopt-or-kill (PID-alive + health + fingerprint match against `engine-state.json`/`engine.json` + health `version` >= `MIN_ENGINE_VERSION`, newer adopted, per P3/Q1); spawn chain (resources engine → `PODCAST_READER_ENGINE_CMD` — parsed by documented whitespace split, paths with spaces unsupported in the override (per P6) — → `uv run podcast-reader serve`); spawned engine child receives `PODCAST_READER_DATA_DIR` matching the app's own resolution (per P9); sentinel-then-discovery readiness with timeout + captured-stderr error; quit sequence (abort the app's `/v1/events` stream → `POST /v1/shutdown` → wait ≤10 s → force-kill, per P1; adopted engines awaited by PID polling, per P7); unit tests with a scripted fake child process

## 3. Key vault & IPC bridge

- [x] 3.1 `src/main/vault.ts`: safeStorage-encrypted `vault.json` in app userData; set/clear/list-providers; `isEncryptionAvailable()=false` → session-memory mode flagged to the renderer; unit tests (plaintext-absent-from-disk sweep)
- [x] 3.2 Push-at-engine-start: on every engine-ready and on key change, decrypt + `PUT /v1/keys` per provider (clear = push empty string); ordering test: keys pushed before the renderer is told the engine is ready
- [x] 3.3 `src/preload/index.ts`: contextBridge API (`jobs`, `events`, `library`, `transcriptHtml`, `settings`, `keys`, `testKey`, `confirmJob`, `dismissJob`, file-drop path resolution via `webUtils.getPathForFile`); contextIsolation/sandbox/nodeIntegration hardening asserted in e2e (token unreachable from renderer)

## 4. Renderer views

- [x] 4.1 Renderer shell: hash router + typed DOM helpers, nav between the four views, engine-status indicator (starting / ready / failed with diagnostics)
- [x] 4.2 Library view: cards (title, source, date) from `GET /v1/library`, refresh on `job_done` events, empty-state CTA, card → Reader
- [x] 4.3 Reader view: main-process fetch of the artifact HTML → sandboxed iframe `srcdoc` with `allow-scripts` only (no `allow-same-origin`); chapter scroll script works; isolation assertions in e2e
- [x] 4.4 New view: paste-URL + drop-file submission; live step progress from forwarded events with record hydration; failed jobs render `{code, message, hint}`; interrupted jobs offer retry (re-submit)
- [x] 4.5 New view confirmations: list `awaiting-confirmation` jobs with source URL, Run (confirm) and Dismiss (delete) actions; focus + surface on protocol arrival
- [x] 4.6 Settings view: provider dropdown populated from `GET /v1/providers` (ids, default models, key-availability; incl. custom + base-URL field) (per P4), masked write-only key entry + Test button (`POST /v1/keys/test` result display), whisper model/device/lang, sentences, library dir; save = `PUT /v1/settings` + vault/push for keys; inline engine validation errors; safeStorage-unavailable warning

## 5. Protocol handler

- [x] 5.1 Single-instance lock; `podcast-reader://` via `app.setAsDefaultProtocolClient` (dev; on Windows dev mode this requires passing `process.execPath` + the app path as args, per P8) + `open-url` (macOS) + `second-instance` argv (Windows — select the `commandLine` entry matching `^podcast-reader://`, never pop blindly, per P8); strict validation (scheme/host `transcribe`/http(s) `url` param), reject-and-log otherwise
- [x] 5.2 Valid protocol URL → `POST /v1/jobs {requires_confirmation: true}` → focus New view; e2e: protocol job never executes without click; malformed URL creates nothing (e2e assertions ride with task 7.2's Playwright suite)

## 6. Packaging & auto-update

- [x] 6.1 electron-builder config: appId, NSIS per-user target (Win), dmg + zip targets (macOS), `protocols` registration, `extraResources` mapping an `--engine-dir` build input → `resources/engine/` (build succeeds without one); unsigned local builds of both targets documented in `app/README.md` — pipeline proven locally via `--linux dir` (engine-less and with a fake `--engine-dir` payload, exec bit preserved); Windows/macOS installer builds belong to the dev-machine check (6.3) and tag CI (6.6)
- [x] 6.2 electron-updater against GitHub Releases: background download, consent prompt, quit-sequence-before-`quitAndInstall`; full-download strategy + revisit-trigger documented (design decision 9); update events surfaced in the UI (`update:status` push + renderer banner with restart-to-update); gated off in dev/unsigned (`updaterGate`; `BUILD_SIGNED` flips in 6.6, `PODCAST_READER_FORCE_UPDATES=1` is the manual unsigned-NSIS seam)
- [ ] 6.3 Unsigned dev build end-to-end check: install locally (Windows or macOS dev machine), protocol registration works, engine handshake completes under the dev posture, one real job runs; macOS open-anyway caveat documented — NOT DONE: requires a Windows/macOS dev machine (implementation host is Linux/WSL); caveats are already documented in `app/README.md`
- [ ] 6.4 [USER-BLOCKING] Windows signing prerequisite: acquire a code-signing identity (Azure Trusted Signing or OV cert) and provision CI secrets — cannot proceed without the user; until then NSIS builds stay unsigned/dev-channel
- [ ] 6.5 [USER-BLOCKING] macOS signing prerequisite: Apple Developer Program enrollment, Developer ID Application cert, notarytool API-key credentials in CI secrets — cannot proceed without the user; macOS auto-update e2e is blocked on this
- [ ] 6.6 [BLOCKED ON 6.4/6.5] Wire `win` signing + `mac.notarize` into electron-builder; tag-pipeline workflow building signed NSIS + notarized dmg/zip on windows/macos runners, publishing to GitHub Releases — stub landed: `.github/workflows/release.yml` is workflow_dispatch-only with a fail-fast signing gate and the runner-job skeleton commented out pending credentials

## 7. Testing & CI

- [x] 7.1 Mock engine (`app/tests/mock-engine/`): TS HTTP server for the `/v1` surface with scriptable job scenarios (SSE sequences, awaiting-confirmation, failures with hints), canned library/transcript HTML, settings echo, keys/test outcomes, shutdown handler; Playwright fixture writes `engine-state.json` + `engine.json` into a temp `PODCAST_READER_DATA_DIR` so the app adopts via its production path
- [x] 7.2 Playwright e2e (mock engine): adopt; stale-discovery → kill/respawn surfaced as spawn-failure messaging (mock dead, file left); four-view flows (4.2–4.6); SSE drop → hydration recovery; key push-at-start ordering; quit sequence (mock asserts the app's events stream closed before the shutdown POST, and the shutdown POST before app exit, per P1); renderer isolation assertions (token unreachable, iframe opaque origin)
- [x] 7.3 Real-engine spawn smoke (integration-marked): launch app with dev fallback against `uv run podcast-reader serve`; assert sentinel → discovery file → authed health → Library renders; additionally assert exact key-set equality of real `JobRecord`/`LibraryEntry`/`EngineSettings` payloads vs the `src/shared/types.ts` mirrors (per Q4); proves the TS handshake mirror matches `engine/process.py`
- [x] 7.4 CI: `node` job in `ci.yml` — npm ci, `tsc --noEmit`, vitest, Playwright e2e under xvfb, then `uv sync` + real-engine smoke; cache npm + Playwright browsers; tag-pipeline installer jobs explicitly deferred (see 6.6) — split as `node` (typecheck/lint/vitest/build + linux-dir packaging proof) and `app-e2e` (xvfb Playwright: mock e2e + real-engine smoke)

## 8. Docs & wrap-up

- [x] 8.1 README: desktop app section (install, dev posture pre-Phase 4: `uv run podcast-reader serve` or `PODCAST_READER_ENGINE_CMD`, unsigned-build caveats); CLAUDE.md: `app/` module rows, new engine endpoints, node commands
- [x] 8.2 Full gates: pytest, mypy strict, ruff, `tsc --noEmit`, vitest, Playwright e2e; `openspec validate electron-app`
- [ ] 8.3 Systems-review of the implementation diff; PR referencing this change
