# Tasks — Floating Video Player

TDD throughout: write the failing test first, then the implementation. Run `uv run pytest -m "not integration"`, `uv run mypy src/`, and `uv run ruff check` for Python; `npm run typecheck|lint|test` in `app/` for TypeScript. Prefix shell commands with `timeout`.

## 1. Types & settings (boundary-first)

- [x] 1.1 Add `MediaInfo` TypedDict and `media_cache_max_bytes: int` to `EngineSettings` in `src/podcast_reader/types.py`; default the new setting (5 GiB) in `engine/settings.py`. Tests for settings round-trip with the new field.
- [x] 1.2 Mirror `MediaInfo` and the settings field in `app/src/shared/types.ts`; the integration smoke enforces key-set parity.

## 2. Engine media core (`engine/media.py`)

- [x] 2.1 Source classification → `kind` (`youtube`/`video`/`audio`/`unavailable`) reusing the existing YouTube-vs-yt-dlp routing; extract `youtube_id`. Unit tests across URL/local/unknown.
- [x] 2.2 `ffmpeg`-based probe (duration, has-video-track) for local + cached files — no `ffprobe`. Subprocess mocked. Test video/audio/garbage.
- [x] 2.3 Cache module: path layout under the data dir, LRU eviction against `media_cache_max_bytes` (enforced on insert), `.part` identity-bound staging, discard-on-failure. Unit tests for eviction order and partial cleanup.
- [x] 2.4 Single-flight lazy acquisition keyed by `source_id` (in-process future-map, independent of the job worker); concurrent joiners share one download. Test the join + the restart-clean path.

## 3. yt-dlp video variant (`ytdlp.py`)

- [x] 3.1 Add a video-download path (format `bv*+ba/b`, ffmpeg merge) alongside the existing audio `-x` path; preserve the structured `download_failed`/`download_auth_required` codes and the managed-copy self-heal. Test the arg builder and the audio-only fallback.

## 4. Engine routes + events (`engine/app.py`)

- [x] 4.1 `GET /v1/media/{source_id}/info` → `MediaInfo`; immediate for local/YouTube, `preparing` + kick-off for uncached remote. Route tests (auth required, kinds, preparing).
- [x] 4.2 `GET /v1/media/{source_id}` via `FileResponse` with Range; `404` when unavailable. Tests for `206`/`Content-Range` and `404`.
- [x] 4.3 Media-prep SSE events on the EventBus carrying `source_id`, never `job_id`, terminating in `ready`. Test event shape + the no-`job_id` invariant.

## 5. Transcript artifact (`html.py`)

- [x] 5.1 Emit `data-start`/`data-end` on `<p>` passages and chapter sections (passages already carry start/end). Test attribute presence + values.
- [x] 5.2 Add the inline sync script: passage-click → post `seek`; `time` → highlight + scroll the gap-free `[start, next-start)` passage; no-op when `window.parent === window`. Test standalone no-op and the CLI/engine byte-identical output.

## 6. App main process

- [ ] 6.1 `registerSchemesAsPrivileged` for the internal `app://` scheme at module top-level (before `whenReady`); `protocol.handle('app', …)` at ready. Unit/e2e for registration.
- [ ] 6.2 `media-protocol.ts`: validate `^[0-9a-f]{64}$`, loopback-only, add bearer, forward `Range`, return engine `Response` verbatim. Tests for id validation, header forwarding, verbatim status.
- [ ] 6.3 `mediaInfo(sourceId)` IPC + preload bridge typing; engine-client method. Tests for the IPC round-trip.

## 7. Renderer

- [ ] 7.1 `media-player.ts`: floating draggable/resizable panel, geometry persistence, video/audio/YouTube render paths behind `{seekTo, onTime}`, minimize, YouTube embed-disabled fallback. Vitest for kind→skin selection, geometry persistence, the raw-postMessage YouTube control wiring.
- [ ] 7.2 `sync-bridge.ts`: parent-side `pr-sync` protocol with channel + `event.source` filtering; throttled time posts; seek handling. Vitest for filtering (drops foreign/cross-frame messages).
- [ ] 7.3 `views/reader.ts`: mount the player beside the artifact iframe, `mediaInfo` → kind, `preparing` state awaiting SSE `ready` (info re-fetch fallback), `src` set only when ready, teardown on cleanup. Vitest + e2e.

## 8. CSP

- [ ] 8.1 Add `media-src app:` and `frame-src https://www.youtube-nocookie.com` to the meta CSP in `renderer/index.html`; **no** `script-src` youtube allowance. Note the inherited-CSP caveat in the comment.

## 9. Tests & CI

- [ ] 9.1 Extend `app/tests/mock-engine/` with `/v1/media/{id}` + `/info` serving tiny fixture mp4/mp3 (with Range).
- [ ] 9.2 Playwright e2e: player mount, click-to-seek, highlight-follow for a video and an audio fixture.
- [ ] 9.3 YouTube path: unit coverage for URL/seek/control wiring + a documented manual check (precedent: V5 toolbar-popup).

## 10. Docs

- [ ] 10.1 Update `CLAUDE.md` (new modules + the `/v1` media surface row), `app/README.md` (the player + the `app://` scheme), and the engine env/settings table (`media_cache_max_bytes`).
