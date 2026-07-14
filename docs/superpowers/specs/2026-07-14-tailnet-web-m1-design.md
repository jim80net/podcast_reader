# Private tailnet web app — M1 design

## Outcome and scope

From a phone or laptop already admitted to the operator's Tailscale tailnet:

1. open the desktop machine's private HTTPS URL;
2. pair once with the existing six-character code minted in the desktop app;
3. see a read-only library; and
4. open and read any transcript.

M1 adds no submission, jobs, settings, or media playback. It does not start the
iOS, Android, or macOS initiatives. `podcast_reader/html.py` remains the only
transcript renderer; the web reader embeds the already-generated artifact.

## Facts established from the existing system

- `process.bind_engine_socket` pre-binds and listens on
  `127.0.0.1:<persisted-or-ephemeral-port>` before discovery is written. Uvicorn
  receives that exact listening socket. The engine never binds a LAN or tailnet
  interface.
- Tailscale Serve officially supports an HTTPS reverse proxy to a local HTTP
  service and specifically supports `http://127.0.0.1:<port>`. Serve terminates
  tailnet HTTPS, obeys tailnet access rules, and is distinct from Funnel, the
  public product. Serve can run in the foreground or persist with `--bg`. See
  [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve)
  and the [Serve CLI reference](https://tailscale.com/docs/reference/tailscale-cli/serve).
- The extension already implements the correct pairing state machine:
  `claimToken` -> authenticated `health()` -> `ExtensionStore.setPairing()`.
  Failed claim/verify never replaces a previously stored pairing. Its durable
  bearer lives only in `chrome.storage.local`, never sync storage or a URL.
- The engine's unauthenticated `/v1/pair/claim` deliberately rejects every
  HTTP(S) Origin so a hostile page cannot burn the five-attempt budget. That
  route must remain unchanged for extension compatibility and defense-in-depth.
- `/v1/library` and `/v1/transcripts/{source_id}.html` already provide the data
  and artifact required by M1. Existing library entries also contain filesystem
  paths and the original source, which a web client does not need.

## Decision 1: transport stays Tailscale Serve over the existing socket

M1 keeps the engine socket exactly as-is. After explicit user enablement, the
desktop supervisor owns a foreground Serve lease configured against a
product-owned loopback gate:

```text
tailscale serve --https=443 http://127.0.0.1:<guardian-gate-port>
```

The private app URL is the HTTPS URL printed by Tailscale plus `/web/`.

Serve supports exactly this loopback HTTP target. The guardian pre-binds its
gate on `127.0.0.1:0`, then proxies only while its Electron lease is live to the
exact already-listening engine address reported by the existing discovery
handshake. It never asks Uvicorn to re-bind and never targets a guessed or
persisted engine port. TLS certificates and tailnet ACL enforcement remain
Tailscale's responsibility. The engine keeps its bearer and pairing boundary as
an independent application-level control. Tailscale identity headers are
neither trusted nor persisted in M1; pairing works for ordinary and tagged
devices and does not acquire an implicit identity-header dependency.

Settings provides an explicit "Enable private web access" action. Main-process
code discovers the Tailscale CLI, starts Serve only after the engine reports
ready, captures the private URL, and shows actionable unavailable/error states;
failure never affects desktop or extension use. The enabled preference and an
expected Serve record (listener, exact target, and product generation) are app
state, but the Serve mapping itself is deliberately not `--bg`.

The product never assumes that HTTPS listener 443 belongs to it. Before any
Serve mutation it parses `tailscale serve status --json`:

- an empty listener can be claimed and its exact resulting mapping recorded;
- a mapping is product-owned only when it exactly matches the persisted
  listener, loopback target, and product generation;
- an occupied listener without that exact ownership proof is a conflict. The
  app leaves it untouched and explains how to choose between the existing
  service and Podcast Reader private access; and
- cleanup, disable, restart, and shutdown may remove only an exact owned
  mapping. A changed mapping is treated as operator-managed and preserved.

The product generation changes when the transport schema changes, not on every
launch. Ownership is a tiny crash-consistent journal in Electron's user-data
directory. Before mutating Serve, the app atomically writes and fsyncs a
`pending` record containing the listener, generation, and desired gate target;
after status verifies the exact mapping it atomically promotes that record to
`active`. The active record is retained throughout teardown and removed only
after status verifies the mapping absent, with the removal fsynced as well.
Startup reconciliation treats a pending or active record plus an exact matching
status as product-owned and removes it before proceeding; an empty status clears
the stale record; any mismatch is a conflict and neither mapping nor record is
silently changed. A crash can therefore strand a recoverable pending record,
not an ownerless mapping. The target port may be refreshed only through the
remove-then-start sequence below. Unit fixtures inject a crash before and after
each journal/mutation boundary and cover unrelated root and path mappings,
including an unrelated mapping that happens to target the engine port.

Main owns one fail-closed lifecycle:

1. Before every engine spawn while the preference is enabled, inspect Serve
   status. Remove and await a stale mapping only when the exact persisted
   ownership record matches; on a conflict, disable this launch's private-web
   transport without changing Serve and continue ordinary desktop startup.
2. Spawn the engine and wait for its pre-bound ready/discovery handshake.
3. Give the discovered engine address to a fresh guardian, let it pre-bind the
   gate, start foreground Serve against the gate port, and verify
   `tailscale serve status --json` reports that exact gate target before showing
   the URL.
4. On engine failure, manual restart, preference disablement, or app shutdown,
   close the lease and await verified removal before stopping or respawning the
   engine. The app-start ownership check also repairs an unclean prior app exit.

The foreground process is held by a small packaged Serve guardian rather than
being a bare Electron child. Electron gives the guardian an anonymous lease
pipe; the guardian owns both the loopback gate and the foreground Tailscale
child. EOF is the cleanup signal, so normal quit and abrupt parent death use the
same path. Cleanup first stops accepting proxy work but retains the bound gate
socket, removes and verifies the exact Serve mapping, and only then releases the
socket. Thus a stale mapping can never land on a reused engine or gate port.

On Windows the guardian assigns the Serve child to a
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` Job Object. On shipped POSIX targets it
owns a dedicated process group and installs a parent-death signal for the Serve
child in addition to the lease-EOF cleanup. Unsupported targets fail the
private-web capability closed. The guardian does not use `--bg`; it reports
ready only after status proves the exact target and reports stopped only after
status proves that exact owned mapping is gone. This extends the repo's existing
Windows Job Object/POSIX process-group machinery instead of assuming Node child
ownership survives a hard kill.

Therefore a port-collision fallback occurs only while the old mapping is off;
the replacement mapping is created after the new port is known. Forced-parent-
death integration tests kill Electron without running its quit hooks and assert
the gate stops proxying immediately, remains bound until the mapping is gone,
and cannot route to a process that takes the former engine port.
Separate Windows and POSIX lifecycle tests cover Job-close/lease-EOF behavior;
status fixtures inject collisions, Serve failure, restart, shutdown, a killed
parent, and an unrelated mapping. The app must not ship the feature on a target
where the guardian's parent-death proof is unavailable.

The existing Settings pairing view already exposes the current engine port.
Copy will be generalized from "browser extension" to "connect another device"
and will show the six-character code separately while preserving the combined
`<port>-<code>` value for the extension.

The engine normally reuses its persisted port. If it falls back because that
port is occupied, it persists the replacement before announcing ready; the
guardian receives that address only after readiness and never changes an
existing gate's upstream in place. The guardian makes the no-stale-target claim
a tested lease and proxy property, not an inference from Tailscale foreground
mode.

No product code or documentation invokes `tailscale funnel`, opens a public
listener, or binds `0.0.0.0`. The operator can independently reconfigure their
Tailscale daemon outside the product; that external administrator action is the
threat-boundary exception to the product's "never public" guarantee. M1's setup
and tests use Serve only.

Rejected alternatives:

- **Bind the engine to a tailnet/LAN address:** expands every API route's network
  exposure, changes the proven pre-bind/discovery model, and shifts TLS into the
  product. It needs a separate security review and is not the fallback in M1.
- **Tailscale identity as the only login:** weakens the explicit pairing outcome,
  behaves differently for tagged devices, and makes local header spoofing part
  of the auth boundary. Identity headers may become audit metadata later.
- **Persistent/manual `--bg` Serve:** cheap but not fail-closed. If another
  process takes the persisted engine port while the engine is down, the stale
  mapping can expose that unrelated service to the tailnet. The managed
  foreground lease is required for M1.

## Decision 2: a dedicated web boundary, not cookie access to `/v1`

The FastAPI process serves a small browser shell and a read-only browser API:

| Route | Authentication | Purpose |
| --- | --- | --- |
| `GET /web/` | public inside the tailnet | shell; shows pairing or the app |
| `GET /web/assets/app.js` | public | external browser module, no inline code |
| `GET /web/assets/app.css` | public | editorial shell styles |
| `POST /web/api/pair/claim` | same-origin HTTPS + pairing code | web-specific claim |
| `GET /v1/health` | candidate bearer | reuse the existing pairing verifier |
| `POST /web/api/session` | candidate bearer + same-origin HTTPS | set web cookie |
| `GET /web/api/library` | web session cookie | minimized library projection |
| `GET /web/api/transcripts/{source_id}.html` | web session cookie | existing artifact |
| `POST /web/api/logout` | web session cookie + same origin | clear this browser |

The current bearer middleware keeps protecting every `/v1` route exactly as it
does today. Public web paths and cookie-authenticated web paths are explicit
route patterns, never a blanket `/web` bypass. The web cookie is accepted only
by the web-session dependency; it can never authorize jobs, settings, keys,
packs, shutdown, or any other `/v1` operation.

`/web/api/library` projects each engine `LibraryEntry` to only `source_id`,
`title`, and `created_at`. It does not expose `html_path`, local input paths, or
source URLs/query strings. The transcript route resolves the id through the
same library helpers as `/v1/transcripts`; arbitrary paths never become route
inputs.

The browser shell is intentionally small browser-native JavaScript and CSS
served as package data. It uses DOM construction and `textContent`, never
`innerHTML`; it has no service worker and no third-party runtime dependency.
The Electron Library/Reader views are the layout and wording reference, while
the transcript itself is the exact `html.py` artifact. This avoids coupling the
frozen Python engine to a second Node build pipeline for a two-view shell.

## Decision 3: claim -> verify -> store, with no browser-readable durable bearer

The browser mirrors the extension flow rather than inventing a password system:

1. The desktop app calls the existing authenticated `POST /v1/pair`; the single
   in-memory, single-use, five-minute code and five-attempt budget are unchanged.
2. The browser posts the code to `/web/api/pair/claim`. This route shares the
   same `PairingState` and response shape as `/v1/pair/claim`, but has a web-only
   origin gate. A successful claim returns the engine bearer over tailnet HTTPS.
3. The module holds the candidate bearer in one function-local variable and
   calls authenticated `GET /v1/health`.
4. Only after health succeeds, it calls authenticated
   `POST /web/api/session`. The engine returns a separate signed web-session
   credential as a cookie.
5. The module drops its reference and uses `location.replace('/web/')`; the
   pairing realm is destroyed before the library renders. A failed claim or
   verify creates no cookie and does not replace a previously valid session.

The engine bearer is never written to `localStorage`, `sessionStorage`,
IndexedDB, Cache Storage, a service worker, the DOM, a URL, browser history, or
a JavaScript-readable cookie. The claim and session responses use
`Cache-Control: no-store`. As in the extension, the claim response is the one
intentional bearer-bearing response; it is never logged.

### Web session format and cookie

The session credential is a versioned random nonce plus issued/expiry times,
authenticated with HMAC-SHA256 using the engine bearer as the signing key. It
contains no bearer or library data, needs no server-side session file, survives
an engine restart, and becomes invalid if the engine bearer rotates. Signature
comparison is constant-time. The fixed lifetime is 180 days; logout clears this
browser's cookie. A global "revoke web sessions" control can rotate a dedicated
signing generation later without changing the browser contract.

Cookie attributes:

```text
__Secure-podcast_reader_web=<signed credential>;
Secure; HttpOnly; SameSite=Strict; Path=/web/; Max-Age=15552000
```

No `Domain` attribute is set. The `__Secure-` prefix is used instead of
`__Host-` because the latter mandates `Path=/`; keeping this cookie scoped to
`/web/` prevents it from riding ordinary `/v1` requests. The signed credential
is itself a secret and joins the K4 redaction corpus.

### Web claim and CSRF gate

The existing extension claim endpoint remains unchanged. The new web claim is
accepted only when all of these hold:

- `Content-Type` is `application/json`;
- numeric `Content-Length` is present and no more than 4096 bytes;
- `Origin` is HTTPS and exactly matches the normalized request `Host`; and
- `Sec-Fetch-Site` is `same-origin`.

There is no CORS middleware and no `Access-Control-Allow-Origin` response. A
foreign page cannot send the claim POST or burn its attempt budget. Gate
failures remain a uniform self-authored 403 and never call `PairingState.claim`.
The same exact-origin rule applies to session creation and logout. M1's data
routes are GET-only and the cookie is `SameSite=Strict`; future write routes
must add an explicit CSRF-token design rather than inheriting read-only rules.

## Decision 4: transcript isolation and CSP

The reader uses:

```html
<iframe sandbox="allow-scripts" src="/web/api/transcripts/<source_id>.html">
```

It deliberately omits `allow-same-origin`, forms, popups, and navigation
permissions. The authenticated navigation carries the HttpOnly cookie, then the
artifact executes in an opaque origin with no parent DOM, web storage, or cookie
access—matching the Electron Reader's existing isolation model. Theme changes
use the artifact's existing `postMessage` listener.

The outer shell uses an external-script-only policy:

```text
default-src 'none'; script-src 'self'; style-src 'self';
connect-src 'self'; frame-src 'self'; img-src 'self' data:;
base-uri 'none'; form-action 'self'; frame-ancestors 'none'; object-src 'none'
```

Transcript responses get this separate artifact policy (the hashes below are
generated values, not literals in the design):

```text
default-src 'none';
script-src <SHA-256 hashes of the exact emitted script text nodes>;
style-src 'unsafe-inline' https://fonts.googleapis.com;
font-src https://fonts.gstatic.com;
img-src data:;
connect-src 'none'; media-src 'none'; frame-src 'none';
object-src 'none'; base-uri 'none'; form-action 'none';
frame-ancestors 'self'
```

The script hash helper covers the exact bytes between each emitted `<script>`
and `</script>` tag, including the leading newline added by `build_html`; it
does not hash the bare constants and assume serialization. Chaptered artifacts
carry scroll+sync hashes, keyless artifacts carry rail+sync hashes, and empty
artifacts carry the applicable sync hash. Unit tests extract the actual emitted
text nodes for every conditional combination, and real-browser tests fail on
any CSP violation. `style-src 'unsafe-inline'` is limited to the opaque
sandbox's engine-generated renderer CSS; no secret is ever present there.

No token, cookie value, source URL, or local path is interpolated into shell or
artifact markup. Every web response sets `Referrer-Policy: no-referrer` and
`X-Content-Type-Options: nosniff`; authenticated HTML/API responses also set
`Cache-Control: no-store`.

## Decision 5: media-element authentication is the same scoped cookie

M1 renders transcript-only and does not call media info, prepare downloads, or
mount `<audio>`, `<video>`, or YouTube. The later media route is nevertheless
decided now:

```text
GET /web/api/media/{source_id}
```

It will validate the same scoped HttpOnly web-session cookie and return the
existing `FileResponse`, preserving browser Range requests. Native media
elements automatically attach same-origin cookies, solving the header problem
without exposing the engine bearer. We reject short-lived query-string media
tokens because URLs leak into history, referrers, screenshots, diagnostics, and
access logs. We also reject teaching the web cookie to authenticate `/v1/media`;
the read-only web boundary remains least-privilege. This route and all player UI
remain M2 implementation work.

## Packaging

Static shell files live under the Python package and are resolved with
`importlib.resources`, not the current working directory. Hatch package data
and `packaging/engine.spec` explicitly collect the directory. Frozen smoke must
request the shell, JS, and CSS from the actual packaged engine; missing assets
fail the build. No Tailscale binary is bundled and the engine does not depend on
Tailscale to continue serving desktop/extension clients.

The guardian is a `serve-guardian` subcommand of the existing frozen
`podcast-reader-engine` executable, not an untracked script or a second Python
environment. The Electron engine-command resolver supplies the same packaged,
override, and development launch postures it already uses for `serve`.
Installer/frozen smoke launches the real subcommand, connects through its gate,
closes the lease, and proves that the gate and a fake foreground Serve child are
reaped. Platform lifecycle tests exercise the real Windows Job Object and POSIX
parent-death/process-group paths. The real Tailscale daemon remains an operator
acceptance dependency, not a bundled build input.

## K4 and verification plan

### Unit/API gates

- Existing loopback bind and pre-bound socket tests stay byte-for-byte in force.
- Extension `/v1/pair/claim` behavior and Origin rejection remain unchanged.
- Web claim tests cover exact HTTPS Origin/Host, `Sec-Fetch-Site`, JSON/body
  bounds, uniform failures, and prove rejected gates do not consume attempts.
- Session tests cover signature tampering, expiry, constant-time verification,
  engine-bearer rotation, cookie flags/path, logout, and rejection on `/v1`.
- Route tests prove unauthenticated shell assets contain no data, web data needs
  the cookie, `/v1` still needs bearer, and the minimized library DTO contains
  no source/path fields.
- Transcript tests prove id validation, sandbox/CSP headers, and byte identity
  with the existing committed `html.py` artifact.

### Browser gate

A real-browser test runs at narrow phone and desktop widths:

1. unauthenticated `/web/` shows the pairing view;
2. mint a real code, claim, verify, and store;
3. library titles render and a transcript opens in the opaque sandbox;
4. reload retains the session; logout removes it;
5. the engine bearer and distinctive web-session prefix are absent from the
   URL/history, DOM, console, local/session storage, IndexedDB, Cache Storage,
   static assets, response bodies other than the intentional claim, and server
   logs. The engine bearer has exactly one allowlisted persisted copy in
   `<data_dir>/engine-state.json` and no new copy. On POSIX its existing mode is
   exactly `0600`. Windows mode bits do not prove secrecy, so M1 makes a
   restricted DACL an engine-state invariant: every create and atomic rewrite
   applies and verifies the DACL before the destination is exposed, then
   verifies the destination after replacement. Current user and SYSTEM may read
   it; broad principals such as Everyone, Users, and Authenticated Users may
   not. The signed web-session credential is absent from app-managed and engine data
   files; its sole expected durable owner is the browser's cookie database; and
6. `document.cookie` cannot see the HttpOnly credential. The browser harness
   may inspect the cookie jar only to assert the one expected scoped secret and
   its exact flags.

The K4 sweep scans both full secrets and distinctive prefixes. Its filesystem
allowlist asserts the exact state-file path plus the platform-specific access
control above rather than silently skipping the data directory. Tests verify
the Windows DACL after first creation, a port-fallback rewrite, and token
rotation. Private-web startup re-verifies the current file and fails closed if
the invariant is not met, while a failure leaves existing local use available
with an actionable error. Browser
inspection asserts exactly one scoped cookie and no copies in other browser
storage or cache. Error-path cases inject each credential into malformed input
and mocked failures so validation, logging, and exception handling cannot
reflect them.

### Transport proof

CI cannot establish the operator's tailnet, but its browser proof does exercise
the production HTTPS boundary. The test starts a local Node HTTPS reverse proxy
with a checked-in test-only certificate for controlled `web.test`,
`evil.web.test`, and `evil.test` hostnames, maps those names to `127.0.0.1` in
Chromium, and enables `ignoreHTTPSErrors` only in that test context. The proxy preserves the external
`Host` and `Origin` while forwarding to the real loopback engine. The browser
must complete the real Secure-cookie claim -> verify -> store -> reload flow;
direct loopback HTTP must fail. `evil.web.test` is the same site but a different
origin: the host-only cookie still goes only to the framed `web.test` request,
so a failed embedding proves `frame-ancestors 'self'` rather than merely proving
that SameSite withheld authentication. The separate cross-site `evil.test`
origin proves foreign claim/CSRF attempts are rejected, while the same-origin
transcript iframe succeeds.

The same browser cases load chaptered and keyless artifacts, assert no CSP
violations, and thereby prove the exact conditional script hashes and
`frame-ancestors 'self'` behavior. CI additionally proves loopback binding,
managed Serve lifecycle ordering, packaged assets, and command/status parsing.

The final transport acceptance proof still requires the operator's real
tailnet: enable the managed Serve control, then pair and read one transcript
from a second tailnet device. The PR will state that limitation plainly.

## Implementation order after the design gate

1. TDD the signed session primitive and the separate web auth dependency.
2. TDD the web claim gates and route classification without changing extension
   pairing behavior.
3. Add minimized library/transcript routes and security headers.
4. Add the external shell assets and pairing/library/reader browser flow.
5. Extend K4, browser, package-data, and frozen-smoke gates.
6. Add the explicitly enabled foreground Serve manager, lifecycle race tests,
   status/repair UX, and the full review stack.

Implementation does not begin until this design has passed systems-review,
open-code-review, and the podcast-reader-xo/operator surface gate.
