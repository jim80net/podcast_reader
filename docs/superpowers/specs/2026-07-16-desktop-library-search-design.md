# Desktop Library Search — SS-2 Design

## Decision

The desktop Library uses a new bearer-authenticated `POST /v1/search` route.
Both it and `POST /web/api/search` call one engine helper, sharing query
validation, the bounded `engine/search.py` scan, minimized results, completeness
flags, and one process-wide nonblocking lock. Electron does not use the web
route: browser Origin and signed-cookie gates belong to that remote surface,
while the engine bearer stays in Electron's main process.

Queries are JSON request bodies, never URL parameters. Accepted queries are
trimmed, 2–100 characters, and at most eight whitespace-separated terms.
Responses contain only `source_id`, `title`, and `excerpt`, capped by the core
at 20 matches, plus `has_more` and `partial`. Success and busy responses are
`no-store`; validation, busy, and failure copy never reflects the query.

## Desktop boundary and UI

`EngineClient` owns the authenticated loopback request. The renderer receives
only the query/result payload through the typed IPC/preload bridge; the bearer
never crosses it. The Library view provides a Settings-style labeled search
field with privacy-safe autocomplete/spelling attributes, a 250 ms debounce,
fixed status copy, plain-text result rendering, Clear, and Retry. Every input
change, refresh, and disposal invalidates prior generations so stale results or
errors cannot repaint the current view. Busy responses get at most two retries,
scheduled only while the current generation remains inside a three-second retry
window. Empty or one-character input restores
the cached full Library without making a search request.

## Verification

Engine tests pin bearer auth, POST-body privacy, redacted validation, minimized
results, `no-store`, and shared web/desktop contention. Client and IPC tests pin
the credential-free bridge. Electron Playwright covers search, excerpt display,
clear/focus, query-free URLs/logs, Reader navigation, and a blank remount.
Existing `test_search.py` remains the exhaustive proof for normalization,
canonical parsing, AND matching, excerpts, and all scan limits.
