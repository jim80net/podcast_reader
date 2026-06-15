"""Engine-hosted YouTube embed page.

YouTube's embedded player rejects requests whose embedding context is not a
valid HTTP origin: a desktop app whose renderer is a ``file://`` document gets
"Error 153" (no Referer) and then "Error 152" (origin not allowed). Serving the
embed from the engine's loopback HTTP server gives the player iframe a real
``http://127.0.0.1:<port>`` origin — the same thing a developer embedding on
``localhost`` has — which YouTube accepts.

The page hosts the official YouTube IFrame Player API and exposes a tiny
``postMessage`` protocol to its parent (the app's Reader iframe):

  parent → page : ``{source: "pr-embed-cmd", type: "seek", seconds}``
  page → parent : ``{source: "pr-embed", type: "ready" | "time" | "error", ...}``

The ``error`` event (e.g. the video owner disallows embedding) lets the app
fall back to a "Watch on YouTube" button. The protocol source tags are a
contract shared with ``app/src/renderer/src/embed-protocol.ts`` — a unit test on
each side pins the literals so they cannot drift apart.
"""

from __future__ import annotations

import html
import re

# YouTube video ids are 11 chars of [A-Za-z0-9_-] in practice; allow a little
# slack but keep it strict enough to reject paths/traversal/script injection.
VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

#: Source tag for events the page posts to its parent (page → app).
EMBED_EVENT_SOURCE = "pr-embed"
#: Source tag for commands the app posts to the page (app → page).
EMBED_COMMAND_SOURCE = "pr-embed-cmd"


def is_valid_video_id(video_id: str) -> bool:
    """True when *video_id* is a safe YouTube id (no traversal/injection)."""
    return VIDEO_ID_PATTERN.match(video_id) is not None


def build_embed_page(video_id: str) -> str:
    """Return the self-contained HTML embed page for *video_id*.

    The caller MUST validate *video_id* with :func:`is_valid_video_id` first;
    the id is still HTML/JS-escaped here as defense in depth.
    """
    safe_id = html.escape(video_id, quote=True)
    # The id is embedded as a JSON string literal in the script; json-style
    # escaping plus the alnum/-/_ validation upstream makes injection moot.
    js_id = f'"{safe_id}"'
    return _PAGE_TEMPLATE.replace("__VIDEO_ID__", js_id)


# Kept as a plain string (no f-string) to avoid brace-escaping the JS. The only
# substitution is the JSON-quoted, validated video id at __VIDEO_ID__.
_PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>YouTube</title>
<style>
  html, body { margin: 0; height: 100%; background: #000; overflow: hidden; }
  #player { width: 100%; height: 100%; }
</style>
</head>
<body>
<div id="player"></div>
<script>
(function () {
  var VIDEO_ID = __VIDEO_ID__;
  var EVENT_SOURCE = "pr-embed";
  var COMMAND_SOURCE = "pr-embed-cmd";
  var player = null;
  var timer = null;

  function post(msg) {
    msg.source = EVENT_SOURCE;
    try { window.parent.postMessage(msg, "*"); } catch (e) { /* parent gone */ }
  }
  function stopTimer() { if (timer) { clearInterval(timer); timer = null; } }
  function startTimer() {
    stopTimer();
    timer = setInterval(function () {
      if (player && typeof player.getCurrentTime === "function") {
        var t = player.getCurrentTime();
        if (typeof t === "number" && isFinite(t)) post({ type: "time", seconds: t });
      }
    }, 250);
  }

  window.addEventListener("message", function (e) {
    var d = e.data;
    if (!d || d.source !== COMMAND_SOURCE) return;
    if (d.type === "seek" && player && typeof player.seekTo === "function") {
      player.seekTo(d.seconds, true);
    }
  });

  // Called by the YouTube IFrame API once it loads.
  window.onYouTubeIframeAPIReady = function () {
    player = new YT.Player("player", {
      videoId: VIDEO_ID,
      host: "https://www.youtube-nocookie.com",
      playerVars: { playsinline: 1, rel: 0, origin: location.origin },
      events: {
        onReady: function () { post({ type: "ready" }); },
        onStateChange: function (ev) {
          if (ev.data === YT.PlayerState.PLAYING) startTimer();
          else if (ev.data === YT.PlayerState.PAUSED) stopTimer();
          else if (ev.data === YT.PlayerState.ENDED) stopTimer();
        },
        onError: function (ev) { stopTimer(); post({ type: "error", code: ev.data }); }
      }
    });
  };

  var s = document.createElement("script");
  s.src = "https://www.youtube.com/iframe_api";
  s.onerror = function () { post({ type: "error", code: -1 }); };
  document.head.appendChild(s);
}());
</script>
</body>
</html>
"""
