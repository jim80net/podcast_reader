# Floating Video Player

## Why

The desktop app's Reader view renders a transcript artifact in a sandboxed iframe and nothing else. The original packaging design and the archived electron-app design decision 8 both reserved this view as the home of a future floating video player ("the future video player mounts beside the iframe in this view") and explicitly deferred a privileged custom protocol with per-response CSP "to the video-player phase." This is that phase. The value is *watch/listen while reading*, with the transcript and media kept in lockstep: click a passage to seek the media, and have the current passage highlight and scroll into view as the media plays. Because podcast_reader is predominantly an audio tool, the same synchronization must serve audio-only entries, not just video.

## What Changes

- **New floating media player** in the Reader view: a draggable, resizable picture-in-picture panel layered over the transcript. Video sources render `<video>`; audio-only sources render a compact `<audio>` bar; both drive the same sync. Geometry persists per session.
- **Playback for all source classes.** YouTube embeds via a cross-origin `youtube-nocookie` iframe driven by the **raw YouTube iframe `postMessage` control protocol** (no third-party JS in the renderer's `window.api` context — per design F1); local files stream from disk; arbitrary remote sources are **lazily downloaded on first watch** into an LRU-capped cache and streamed.
- **Engine media surface (additive):**
  - `GET /v1/media/{source_id}/info` → `{kind, youtube_id, duration_s, status, progress}` where `kind ∈ {youtube, video, audio, unavailable}`; classification and probing live in the engine (one place).
  - `GET /v1/media/{source_id}` → serves cached/local bytes with HTTP **Range** via Starlette `FileResponse` (real 206 partial content for seeking — per design F5).
  - Media-prep download progress on the existing SSE `EventBus`, carrying `source_id` (never `job_id`, the same separation pack events observe).
  - New `engine/media.py`: media cache (LRU eviction, single-flight lazy download reusing `ytdlp.py` with a video format selector, `.part` staging), `ffmpeg`-based probing (no `ffprobe` dependency — per design F8).
  - New `EngineSettings.media_cache_max_bytes` (default 5 GiB).
- **App main process (additive):** register an internal privileged `app://` scheme (before `whenReady` — per design F3); `app://media/<source_id>` reverse-proxies the engine route, validating the sha256 `source_id`, adding the bearer token the renderer cannot hold, forwarding the inbound `Range` header, and returning the engine `Response` verbatim. New `mediaInfo` IPC on the preload bridge. Media **bytes** never cross IPC — the media element loads `app://` directly.
- **Renderer (additive):** `media-player.ts` (the floating panel), `sync-bridge.ts` (parent side of the sync protocol), and `reader.ts` extended to mount the player beside the artifact iframe, wire the bridge, and show a `preparing` state while a remote source downloads.
- **Transcript artifact (`html.py`):** each `<p>` passage and chapter section gains `data-start`/`data-end` seconds, and an inline sync script is added that **no-ops when the file is opened standalone** (`window.parent === window`) so the artifact stays self-contained. CLI and engine output identically.
- **CSP deltas (one inherited meta tag):** add `media-src app:` and `frame-src https://www.youtube-nocookie.com`. No `script-src` allowance for youtube.com (F1's raw-iframe approach loads no third-party JS).

No breaking changes: the engine API and the artifact grow additively; the CLI is unaffected except for the harmless additive artifact attributes; the Chrome extension is untouched.

## Capabilities

### New Capabilities

- `media-playback`: the floating media player surface, source-class → player-kind selection, the media cache and lazy-acquisition semantics, and the transcript↔media synchronization protocol.

### Modified Capabilities

- `engine-service`: adds the `GET /v1/media/{id}` (FileResponse Range) and `GET /v1/media/{id}/info` routes and the media-prep SSE event.
- `app-shell`: adds the privileged `app://` media protocol and the `mediaInfo` IPC.
- `app-views`: the Reader view hosts the floating media player and wires the sync bridge, including the `preparing` state.
- `job-pipeline`: the rendered transcript artifact carries per-passage timestamp metadata and an inert-when-standalone sync script.

## Impact

- **Code:** new `src/podcast_reader/engine/media.py`; routes + SSE event in `engine/app.py`; `EngineSettings` field in `types.py`; a video download variant in `ytdlp.py`; `data-*` + sync script in `html.py`. New `app/src/main/media-protocol.ts` + scheme registration in `index.ts` + `mediaInfo` in `ipc.ts`/`preload`; new `app/src/renderer/src/media-player.ts` and `sync-bridge.ts`, extended `views/reader.ts`; CSP meta in `renderer/index.html`. TS mirrors in `app/src/shared/types.ts`.
- **Tests:** pytest for the media cache (LRU/single-flight/`.part`), the `ffmpeg` probe, the Range route, and the yt-dlp video arg builder. App vitest for the sync-bridge filtering, kind→skin selection, geometry persistence, and the `mediaInfo` client. Mock-engine `/v1/media` fixtures + Playwright e2e for player mount, click-to-seek, and highlight-follow. YouTube path: unit coverage + a documented manual check.
- **Security:** renderer stays credential-free (token only in main; `app://` adds it); `source_id` sha256 validation blocks traversal; `app://` proxies only to loopback; the artifact sandbox is unchanged; no third-party JS enters the `window.api` context.
