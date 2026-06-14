# Floating Video Player — Design

**Date:** 2026-06-13
**Status:** Approved design v2, post-systems-review (pre-openspec)
**Author:** Jim Park, with Claude
**Review history:** v1 — design approved in brainstorm (five scope decisions: all sources,
lazy-download + LRU cache for remote, floating in-window overlay, full bidirectional
transcript sync, audio player for audio-only entries; media transport via approach B —
engine owns media, main reverse-proxies with the bearer token). v2 — applies
systems-review findings F1–F8: YouTube via raw-iframe postMessage (no third-party JS in
the `window.api` context, F1); engine serves media via `FileResponse` for real Range
support (F5); `registerSchemesAsPrivileged` timing made explicit (F3); no `ffprobe`
dependency — probe via `ffmpeg`/yt-dlp (F8); explicit `preparing` wait-contract (F4);
CSP-inheritance caveat (F2); gap-free highlight boundaries (F6); audio-only remote
format fallback (F7).

## Problem

The desktop app's Reader view (`app/src/renderer/src/views/reader.ts`) renders a
transcript artifact in an opaque-origin sandboxed iframe and nothing else. The original
packaging design (`docs/superpowers/specs/2026-06-11-desktop-packaging-design.md`,
line 166) and the archived electron-app design decision 8 both reserved this view as the
home of a future floating video player — "the future video player mounts beside the
iframe in this view" — and decision 8 explicitly deferred a privileged custom protocol
with per-response CSP "to the video-player phase." This is that phase.

The value is *watch/listen while reading*, with the transcript and media kept in lockstep:
click a passage to seek the media, and have the current passage highlight and scroll into
view as the media plays. Because podcast_reader is predominantly an audio tool, the same
synchronization must serve audio-only entries, not just video.

## Goals

- A draggable, resizable floating media player layered over the Reader content.
- Playback for **all** source classes: YouTube (IFrame embed, no download), local files
  (stream from disk), and arbitrary remote (lazy download + cache, stream from engine).
- Audio-only entries get a compact floating audio player driving the identical sync.
- **Full bidirectional sync** between the floating player and the sandboxed transcript
  iframe via `postMessage`.
- Preserve every security invariant: credential-free renderer, token in main only,
  opaque-origin artifact sandbox, no token-spraying.
- The transcript artifact remains a working **standalone** file (sync degrades to a no-op
  when opened outside the app).

## Non-goals (out of scope here)

- Serving the transcript artifact itself through the new privileged protocol to drop its
  CSP `unsafe-inline` (decision 8's bonus cleanup) — a noted follow-on, not built here.
- A separate always-on-top OS-level picture-in-picture *window* (a second BrowserWindow
  with cross-process sync) — possible future enhancement; v1 floats within the app window.
- Any Chrome extension change — the extension is a submission surface, not a reader, and
  is untouched.
- Eager video download at transcription time — explicitly rejected for storage cost.

## Architecture

```
┌─ Renderer (sandboxed, credential-free) ───────────────────────────┐
│  Reader view                                                       │
│   ┌─ transcript iframe ─┐      ┌─ floating media-player panel ─┐    │
│   │ sandbox=allow-      │◄────►│  <video>/<audio>  OR          │    │
│   │   scripts (opaque)  │ post │  YouTube <iframe> (IFrame API)│    │
│   │ data-start/-end +   │Message│  drag / resize / persist     │    │
│   │ sync script (no-op  │ pr-sync│                              │    │
│   │ when standalone)    │      └──────────────┬───────────────┘    │
│   └─────────────────────┘                     │ <video src=         │
│                                               │  app://media/ID>    │
└───────────────────────────────────────────────┼───────────────────┘
                                                 │ (no token here)
                          ┌──────────────────────▼───────────────────┐
                          │ Main process                              │
                          │  app:// privileged scheme handler         │
                          │   • validates source_id                   │
                          │   • adds Authorization (bearer)           │
                          │   • passes Range through, streams body    │
                          └──────────────────────┬───────────────────┘
                                                 │ 127.0.0.1:<port>
                          ┌──────────────────────▼───────────────────┐
                          │ Engine (FastAPI)                          │
                          │  GET /v1/media/{source_id}/info  → kind   │
                          │  GET /v1/media/{source_id}       → Range  │
                          │  engine/media.py: LRU cache, single-      │
                          │   flight lazy download (ytdlp video),     │
                          │   ffprobe kind/duration, .part staging    │
                          │  SSE EventBus: media-prep progress        │
                          └───────────────────────────────────────────┘
```

YouTube is special-cased end to end: the engine reports `kind: youtube` + `youtube_id`,
and the renderer embeds the YouTube player directly — **no media bytes flow through the
engine or the `app://` protocol for YouTube.**

**YouTube embedding stays out of the `window.api` context (F1).** The renderer exposes the
credential bridge `window.api` (contextBridge) to its main world. A *cross-origin* YouTube
iframe cannot reach `window.api`, but the YouTube **JS IFrame API** (`youtube.com/iframe_api`
+ its injected `www-widgetapi.js`) would execute in that same main world, making youtube.com
a potential caller of `mediaInfo` / `transcriptHtml` / job submission / cookies. Therefore
the design does **not** load the YouTube JS API. Instead it embeds
`<iframe src="https://www.youtube-nocookie.com/embed/<id>?enablejsapi=1">` and drives it via
the **raw YouTube iframe `postMessage` control protocol** (`{event:'command',
func:'seekTo', args:[t]}`, `listening`/`onStateChange` info events), confining all YouTube
code to its own cross-origin iframe. This also eliminates any `script-src` CSP allowance for
youtube.com — only a `frame-src` allowance remains.

## Components

### Engine — `src/podcast_reader/engine/media.py` (new)
Owns all non-YouTube media. Responsibilities:
- **Classification.** Resolve `source_id` → `LibraryEntry.source`, then classify the
  source (YouTube vs other-remote vs local) reusing the existing URL-routing logic that
  already distinguishes YouTube-captions from yt-dlp downloads (one place; the renderer
  never parses URLs).
- **Probe.** `MediaInfo` = `{kind, youtube_id, duration_s, status, progress}` where
  `kind ∈ {youtube, video, audio, unavailable}` and `status ∈ {ready, preparing,
  unavailable}`. **No `ffprobe` dependency (F8):** the tool seeds bundle `ffmpeg` (proven:
  `diarize.py:121` resolves `ffmpeg`, not `ffprobe`), so duration/has-video-track is read
  from `ffmpeg -i` stderr for local files, or captured from yt-dlp's known format at
  download time for remote sources. YouTube info is immediate (classification only).
- **Lazy download (remote).** First info/byte request for an uncached remote source
  returns `status: preparing` and starts a **single-flight** download — an in-process
  future-map in `media.py` keyed by `source_id`, **independent of the FIFO job worker**;
  concurrent requests for the same id join the in-flight download. It reuses `ytdlp.py`
  with a new video variant: format selector `bv*+ba/b` (best video+audio, **falling back
  to best single stream when there is no video track — F7**, so audio-only posts still
  resolve), ffmpeg merge. Staging uses the `.part` discipline borrowed from
  `pack_manager`; a failed/partial download is discarded, never served. After an engine
  restart mid-download the `.part` is gone and the next request restarts cleanly.
- **Cache + eviction.** Completed media lands in `<data_dir>/media-cache/`, evicted LRU
  by last access against `EngineSettings.media_cache_max_bytes` (default 5 GiB). Eviction
  runs on insert when over cap.
- **Progress.** Download progress publishes on the existing SSE `EventBus` as a media-prep
  event carrying `source_id` (never `job_id` — same separation pack events observe).

### Engine — `src/podcast_reader/engine/app.py` (routes)
- `GET /v1/media/{source_id}/info` → `MediaInfo` JSON. Triggers/awaits the single-flight
  cache fill for remote sources; immediate for local and YouTube.
- `GET /v1/media/{source_id}` → serves the cached/local bytes with HTTP **Range** support
  (206 partial content) so the player can seek. **Implemented with Starlette
  `FileResponse` (F5)**, which provides Range/`Content-Range`/`Accept-Ranges` for on-disk
  files out of the box — a hand-rolled `StreamingResponse` would silently return 200
  full-body and break seeking on large videos. 404/`unavailable` when there is no playable
  media. Both routes are bearer-authed like the rest of `/v1`.

### Main — `app/src/main/media-protocol.ts` (new) + scheme registration
- Register an internal **`app://`** scheme as privileged (standard + secure + stream +
  `supportFetchAPI`) via `protocol.registerSchemesAsPrivileged` **at module top-level,
  before `app.whenReady` (F3)** — index.ts today registers only `setAsDefaultProtocolClient`
  (a different mechanism, OS deep-link), so this is the first privileged-scheme
  registration; calling it after `ready` silently no-ops. The `protocol.handle('app', …)`
  binding itself is installed inside `whenReady`.
  A *distinct* internal scheme (not the existing external deep-link scheme
  `podcast-reader://transcribe`, decision 7) keeps the two mechanisms cleanly separated:
  `podcast-reader://` is the OS-registered handler that *launches* the app with a URL
  (`open-url` / `second-instance` argv); `app://` is a `session.protocol.handle`
  interceptor for in-app resource loads. Different layers, no overlap.
- Handle `app://media/<source_id>`: validate `source_id` against the **sha256 hexdigest
  pattern `^[0-9a-f]{64}$`** (that is exactly what `library.source_identity` produces —
  `library.py:34` — so traversal is impossible), reject anything else (no SSRF — the
  handler only ever targets `127.0.0.1:<port>`), then `fetch` the engine route with the
  `Authorization` header and **the inbound `Range` header passed through**, and **return
  the engine `Response` verbatim** so Electron streams the body and the 206 status +
  `Content-Range` headers propagate to the `<video>` element (no full-file buffering in
  main).
- This realizes the privileged custom protocol decision 8 deferred; the *artifact* served
  through it (to drop `unsafe-inline`) is a future follow-on, not this change.

### Renderer
- `app/src/main/ipc.ts` + `preload/index.ts`: expose `window.api.mediaInfo(sourceId)`
  (typed IPC). Media **bytes** never cross IPC — the `<video>`/`<audio>` element loads
  `app://media/ID` directly.
- `app/src/renderer/src/media-player.ts` (new): the floating panel. Drag (title strip),
  resize (corner), geometry persisted in `localStorage`. Renders by kind: `<video
  controls>`, a slim `<audio controls>` bar, or a YouTube `<iframe>` driven by the IFrame
  API. Exposes a uniform `{ seekTo(t), onTime(cb) }` interface so the sync bridge is
  player-kind-agnostic. Minimize-to-pill and an embed-disabled "Watch on YouTube"
  fallback.
- `app/src/renderer/src/sync-bridge.ts` (new): parent side of the `postMessage` protocol.
- `app/src/renderer/src/views/reader.ts` (extended): fetch `mediaInfo`, mount the player
  beside the existing transcript iframe, wire the bridge, tear both down on view cleanup.

**`preparing` wait-contract (F4).** When `mediaInfo` returns `status: preparing`, the
renderer shows a "preparing video…" state and **waits for the SSE media-prep `ready` event**
for that `source_id` (the existing reconnecting SSE consumer already hydrates from REST
after every reconnect, so a missed event self-heals via an info re-fetch fallback). The
`<video>`/`<audio>` `src="app://media/ID"` is set **only once `ready`** — the element never
points at a half-written cache file.

### Artifact — `src/podcast_reader/html.py`
- Emit `data-start` (and `data-end`) seconds on each `<p>` passage (html.py:278 — passages
  already carry `start`/`end` in their dicts) and chapter sections (currently only chapter
  sections carry `id` anchors and `ts` spans are display-only). **Highlight boundaries are
  gap-free (F6):** the effective end used for "which passage contains `t`" is the *next*
  passage's `start` (last passage clamped to duration), not the raw `data-end` — paragraph
  `end` is the last segment's end and can leave silence gaps that would drop the highlight.
- Add a sync script that is **inert when standalone** (`if (window.parent === window)
  return`): on a passage click it posts `{ch:'pr-sync', type:'seek', t}` to the parent;
  on receiving `{ch:'pr-sync', type:'time', t}` it highlights the passage whose
  `[start,end)` contains `t` and scrolls it into view. The existing sidebar
  IntersectionObserver (`_SCROLL_SCRIPT`) is retained. CLI-generated artifacts gain the
  inert attributes/script harmlessly.

## Sync protocol

Channel-tagged `pr-sync` over `postMessage`. The artifact iframe is opaque-origin
(`allow-scripts`, no `allow-same-origin`), so `event.origin` is unusable — messages are
validated by **`event.source === frame.contentWindow`** plus the channel tag. This dual
filter is mandatory because the **YouTube IFrame API also posts messages to the renderer
`window`**; without it, YT control messages would be mistaken for sync messages.

| Direction | Message | Effect |
|-----------|---------|--------|
| parent → iframe | `{ch:'pr-sync', type:'time', t}` (throttled ~4 Hz) | highlight + scroll the `[start,end)` passage containing `t` |
| iframe → parent | `{ch:'pr-sync', type:'seek', t}` | `player.seekTo(t)` |
| iframe → parent | `{ch:'pr-sync', type:'ready'}` | bridge handshake |

Optional per-mount nonce injected into the srcdoc as defense-in-depth. For YouTube, the
renderer drives `seekTo` and reads playback time via the **raw YouTube iframe `postMessage`
control protocol** (F1) behind the same `media-player` `{seekTo, onTime}` interface, so the
sync bridge code is identical across kinds. Note the dual filter (`event.source` + `pr-sync`
channel) is what keeps the artifact-sync messages and the YouTube-iframe control messages
from being confused, since both arrive as `message` events on the renderer `window`.

## Security / CSP

- **Renderer stays credential-free.** The bearer token never leaves main; the `app://`
  handler adds it. Decisions 4 and 8 preserved; the rejected `webRequest` token-injection
  approach (approach C) stays rejected for the same token-spraying reason.
- **`app://` is privileged but trusted nowhere.** `source_id` is validated against the
  library-key pattern; the handler only proxies to the loopback engine — no arbitrary URL,
  no SSRF.
- **Artifact sandbox unchanged** — `allow-scripts` only, opaque origin, no IPC bridge, no
  token. The sync script runs inside that sandbox and reaches the parent solely via
  `postMessage`.
- **CSP deltas, scoped tight:** `media-src app:`; `frame-src
  https://www.youtube-nocookie.com`. **No `script-src` allowance for youtube.com** — F1's
  raw-iframe approach loads no third-party JS into the renderer. No wildcards.
- **CSP is one inherited meta tag (F2).** The app's CSP is a single `http-equiv` meta in
  `index.html` (line 17) and srcdoc frames *inherit* it (CSP cannot be scoped per-frame —
  see that file's own comment). So `media-src app:` and `frame-src youtube-nocookie` are
  also inherited by the opaque-origin transcript artifact. Risk is low — the artifact is
  our generated, sandboxed content with no IPC bridge and no token — but it widens the
  artifact's embedding/loading powers, which is one more reason to keep deltas minimal
  (F1). The eventual tightening is the decision-8 follow-on: serve the artifact through
  `app://` with its own per-response CSP, then drop `unsafe-inline` from the chrome CSP.

## Types

- Python: `MediaInfo` TypedDict; new `EngineSettings.media_cache_max_bytes: int`; a
  media-prep SSE event shape alongside the existing pack-event shape.
- TS: mirror `MediaInfo` and the settings field in `app/src/shared/types.ts`; key-set
  parity is enforced by the existing integration smoke.

## Testing

- **Python unit:** LRU eviction + single-flight join + `.part` cleanup (subprocess
  mocked, per the project's no-auto-mock-of-environment rule — only the yt-dlp/ffprobe
  subprocess boundary is mocked), `MediaInfo` probe, the Range route (206/full), the
  yt-dlp video arg builder.
- **App vitest:** sync-bridge filtering (drops YT-origin and foreign-`source` messages),
  kind→skin selection, geometry persistence, the `mediaInfo` client.
- **Mock engine + Playwright e2e:** extend `app/tests/mock-engine/` with `/v1/media/{id}`
  + `/info` serving a tiny fixture mp4/mp3, so the player mount, click-to-seek, and
  highlight-follow are exercised without real downloads.
- **YouTube path:** real YouTube can't load in CI → unit coverage for URL/seek logic plus
  a documented manual check (precedent: the V5 toolbar-popup manual check).

## Risks & mitigations

| Risk | Mitigation |
|------|------------|
| YouTube embed-disabled videos / ToS | "Watch on YouTube" fallback; `youtube-nocookie` domain |
| Media-cache disk pressure | LRU cap (`media_cache_max_bytes`); surface usage in Settings (later) |
| postMessage cross-talk with the YouTube IFrame API | mandatory channel + `event.source` filtering |
| Range proxy buffering large files in memory | return the engine `Response` directly; stream, never buffer |
| yt-dlp video formats need ffmpeg merge | ffmpeg already a resolved bundled tool (diarization) |
| Standalone artifact regressions | sync script no-ops without a parent; data attributes inert |

## Follow-ons (tracked, not built here)

1. Serve the artifact via `app://artifact/{id}` with a per-response CSP to drop
   `unsafe-inline` (decision 8 bonus).
2. Optional separate always-on-top PiP window.
3. Settings UI surfacing media-cache usage + a clear-cache action.
