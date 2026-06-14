# Floating Video Player — Design

The full design narrative, diagrams, and rationale live in
`docs/superpowers/specs/2026-06-13-floating-video-player-design.md` (v2, post-systems-review).
This file captures the openspec-relevant decisions and the verified constraints that shape
the spec deltas.

## Five scope decisions (brainstorm)

1. **Source scope: all** — YouTube, local files, and arbitrary remote.
2. **Remote video: lazy download + LRU-capped cache** (not eager-at-transcribe, not
   expiring stream-URL extraction).
3. **Placement: floating, draggable/resizable overlay inside the app window** (not in-pane
   docking, not a separate always-on-top window).
4. **Sync: full bidirectional** — click-to-seek and highlight-and-follow.
5. **Audio-only entries: a media player (audio bar) sharing the identical sync bridge.**

## Media transport: approach B (engine owns media, main proxies)

The renderer is credential-free (bearer token only in the main process — electron-app
design decisions 4 & 8). A renderer-initiated request to the engine carries no auth, and
injecting auth via `session.webRequest` was already rejected (it sprays the token onto
every renderer request). Therefore: the **engine owns all non-YouTube media** (download,
cache, Range serving) and the **main process reverse-proxies** `app://media/<id>` to the
engine route, adding the bearer token. This realizes the privileged custom protocol that
electron-app design decision 8 deferred to "the video-player phase." YouTube bypasses this
entirely — the renderer embeds the player directly.

## Verified constraints (from the code, via systems-review)

- **`source_id` is a sha256 hexdigest** (`library.source_identity`, library.py:34) →
  `app://media/<id>` validates `^[0-9a-f]{64}$`; traversal is impossible.
- **Bearer auth is HTTP middleware** over every `/v1/*` route except `POST /v1/pair/claim`
  (app.py:239) → the new media routes are protected automatically.
- **`ffmpeg` is a resolvable bundled tool** (`resolve_tool("ffmpeg")`, diarize.py:121) but
  **`ffprobe` is not assumed bundled** → probe via `ffmpeg -i` / yt-dlp format (F8).
- **Passages already carry `start`/`end`** (html.py:278) → `data-*` attributes are trivial;
  highlight boundaries use the *next* passage's start to stay gap-free (F6).
- **CSP is a single inherited `http-equiv` meta tag** (renderer/index.html:17) that srcdoc
  frames inherit → every delta is global to chrome and artifact; keep deltas minimal (F2).

## Systems-review findings applied (design v2)

| ID | Severity | Resolution |
|----|----------|-----------|
| F1 | HIGH | YouTube via raw cross-origin-iframe `postMessage`, **not** the JS IFrame API — no third-party JS in the `window.api` context; drops the `script-src youtube.com` CSP delta |
| F5 | MED | engine serves media via `FileResponse` for real HTTP Range; `app://` handler forwards Range + returns the engine `Response` verbatim |
| F3 | MED | `registerSchemesAsPrivileged` at module top-level before `whenReady`; `protocol.handle` inside `whenReady` |
| F8 | MED | no `ffprobe` dependency — probe via `ffmpeg -i` / yt-dlp |
| F4 | MED | explicit `preparing` wait-contract: renderer awaits SSE `ready` (info re-fetch fallback), sets `src` only when ready |
| F2 | MED | documented CSP-inheritance caveat; minimize deltas |
| F6 | LOW | gap-free highlight boundaries (next-passage start, not raw `data-end`) |
| F7 | LOW | `bv*+ba/b` format selector falls back to best single stream for audio-only remote |

## Sync protocol contract

Channel-tagged `pr-sync` over `postMessage`; the artifact iframe is opaque-origin
(`allow-scripts`, no `allow-same-origin`), so messages are validated by
`event.source === frame.contentWindow` **plus** the channel tag — mandatory because the
YouTube iframe's control messages also arrive as `message` events on the renderer window.

| Direction | Message | Effect |
|-----------|---------|--------|
| parent → iframe | `{ch:'pr-sync', type:'time', t}` (throttled ~4 Hz) | highlight + scroll the gap-free `[start, next-start)` passage containing `t` |
| iframe → parent | `{ch:'pr-sync', type:'seek', t}` | `player.seekTo(t)` |
| iframe → parent | `{ch:'pr-sync', type:'ready'}` | bridge handshake |

The player exposes a uniform `{seekTo(t), onTime(cb)}` interface so the bridge is identical
for `<video>`, `<audio>`, and the YouTube iframe (driven by raw `postMessage`).

## Out of scope (tracked follow-ons)

1. Serving the artifact through `app://artifact/{id}` with a per-response CSP to drop the
   chrome's `unsafe-inline` (decision 8 bonus).
2. A separate always-on-top OS-level PiP window.
3. A Settings UI surfacing media-cache usage + clear-cache.
