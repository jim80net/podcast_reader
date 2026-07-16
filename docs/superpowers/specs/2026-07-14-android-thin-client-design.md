# Android thin client — design

## Outcome and authorization boundary

Build a sideloadable Android reader for a person's existing Podcast Reader
engine. The engine and all transcription/chapter work stay on the home Windows
machine; the phone reaches it only through the private Tailscale Serve HTTPS
surface. The native client earns its keep through offline reading and, in the
second milestone, an Android share target.

This document covers the complete client boundary and two delivery milestones:

- **Milestone A:** pair, browse the minimized library, download an artifact,
  and read it online or offline.
- **Milestone B:** accept a shared URL, submit it as a job, and show hydrated
  progress with SSE updates.

The present change is design only. It creates no Android project, API route,
workflow, signing key, or store account. Implementation needs the normal
post-design release and independent review. Google Play enrollment and its
one-time fee remain a separate operator spend gate; Milestone A needs neither.

Out of scope are transcription on the phone, a second transcript renderer,
public/Funnel access, LAN discovery, embedded Tailscale, OAuth, media playback,
editing engine settings, cookies or provider keys, and full desktop feature
parity.

## Established system facts

- The engine remains pre-bound to loopback. The merged private-web guardian
  owns `tailscale serve` and publishes that existing socket without rebinding
  the engine or enabling Funnel.
- `/v1/pair/claim` is the native-client pairing boundary. Android sends bounded
  JSON without an `Origin`; the engine rejects browser `http`/`https` origins
  while deliberately retaining its `chrome-extension://` exception. It consumes
  an in-memory, six-character, single-use code and returns the engine bearer.
- The extension already establishes the safe credential sequence: parse,
  claim, verify with authenticated health, then store. A failed candidate never
  replaces a working credential.
- `/web/api/pair/claim` and the signed `/web/` cookie are browser-specific.
  Their exact-origin and Fetch Metadata gates are not a native protocol and the
  cookie grants only the read-only browser projection.
- `/v1/library`, `/v1/transcripts/{source_id}.html`, `/v1/jobs`,
  `/v1/jobs/{id}`, and `/v1/events` already provide the native data plane.
  Every one requires the engine bearer in `Authorization`; it never belongs in
  a URL.
- `html.py` emits the canonical reader artifact. The Android app must display
  those bytes, not reproduce transcript/chapter layout in Compose.
- The artifact uses only local/system font fallbacks and has no third-party
  font request. The engine removes the exact obsolete import when serving a
  pre-#81 artifact. Already-cached Android copies remain valid documents; the
  Android interceptor still blocks their obsolete remote import.

## Platform and toolchain baseline

Milestones A and B support phones and tablets on Android 9/API 28 or newer,
`arm64-v8a` and `x86_64`. The production APK contains no native code, so those
ABIs are an acceptance/emulator matrix rather than separate binaries. API 28 is
the safe floor: it has Network Security Configuration cleartext-deny behavior,
Keystore AES-GCM, modern exported-component rules can be expressed explicitly,
and maintained WebView/AndroidX support without carrying legacy HTTP or file
access exceptions. The operator's acceptance phone must report API 28+; an
older phone is unsupported rather than a reason to weaken the boundary.

The initial scaffold pins Android `compileSdk`/`targetSdk` 36, Build Tools 36,
AGP 9.2.1, Gradle 9.4.1, and JDK 17. Android documents API 36 as the Android 16
SDK target and AGP 9.2's Gradle/JDK compatibility
([Android 16 SDK](https://developer.android.com/about/versions/16/setup-sdk),
[AGP 9.2](https://developer.android.com/build/releases/agp-9-2-0-release-notes)).
It uses AGP 9.2's built-in Kotlin 2.3.10 (not the incompatible legacy
`kotlin-android` plugin), the matching Compose compiler Gradle plugin, and one
pinned stable Compose BOM/version catalog plus dependency verification and
lockfiles; no dynamic, preview, alpha, or `+` versions. AGP 9 enables built-in
Kotlin by default, and Kotlin 2+ removes the old manual Compose/Kotlin
compatibility-table requirement
([built-in Kotlin](https://developer.android.com/build/migrate-to-built-in-kotlin),
[Compose/Kotlin compatibility](https://developer.android.com/jetpack/androidx/releases/compose-kotlin)).
Upgrades are explicit dependency PRs with unit, lint, emulator, release-build,
and physical acceptance gates; `minSdk` does not rise silently.

The UI is adaptive from 360 dp phones through tablets, portrait/landscape,
font scale 1.0–2.0, light/dark, and TalkBack. ChromeOS, TV, Automotive, Wear,
32-bit-only devices, and desktop Compose are not acceptance targets.

## Decision 1: explicit tailnet HTTPS origin, no discovery

Pairing starts with two values copied from the desktop private-web panel:

1. the managed Serve origin, for example
   `https://desktop.example-tailnet.ts.net`; and
2. the current six-character pairing code.

The app does not scan ports, mDNS, the LAN, MagicDNS short names, or the public
internet. It canonicalizes an entered origin once and accepts it only when all
of these hold:

- the scheme is exactly `https`;
- user info, query, fragment, and non-default port are absent;
- the host is a DNS name ending in `.ts.net`, not an IP literal;
- the path is empty, `/`, or `/web/`, and is stored as the bare origin; and
- IDNA and URI parsing produce one canonical host with no trailing dot or
  encoded authority ambiguity.

This is deliberately narrower than the engine's general
`https-or-localhost` provider-URL policy. Android has no local engine to reach,
and accepting arbitrary HTTPS origins would let a mistyped or malicious setup
screen send the one-time code to a non-tailnet server. A `.ts.net` suffix is not
itself proof of authorization; the actual controls are the Tailscale client's
tailnet membership, Serve's non-Funnel configuration, and TLS hostname
validation. The app uses platform trust, never a trust-all callback, user CA,
or certificate pin. Tailscale provisions HTTPS for a node's tailnet DNS name,
and its docs identify that name as the certificate name
([Tailscale HTTPS](https://tailscale.com/docs/how-to/set-up-https-certificates),
[tailnet names](https://tailscale.com/docs/concepts/tailnet-name)).

The manifest and Network Security Configuration set cleartext traffic false
for the entire app. Release code has no HTTP exception; test builds use an
isolated fake transport rather than a permissive manifest. Android recommends
Network Security Configuration for declarative cleartext opt-out
([Android network security configuration](https://developer.android.com/privacy-and-security/security-config)).

There is no automatic fallback to `/web/`, direct loopback, or a public URL.
An unreachable endpoint means “connect this phone to the same tailnet and make
sure Private web is on”; TLS/host failure means the saved endpoint is rejected.

## Decision 2: claim, verify, then atomically replace the pairing

The pairing state machine mirrors `extension/src/pairing.ts`, with the origin
replacing the extension's loopback port:

```text
unpaired / existing pairing
  -> validate origin and code locally
  -> POST {origin}/v1/pair/claim {code} without Origin
  -> hold candidate bearer in one coroutine scope
  -> GET {origin}/v1/health with Authorization: Bearer <candidate>
  -> encrypt and atomically store {origin, bearer}
  -> connected
```

Claim uses the fixed caps above, JSON content type, no redirects, and no retry.
A redirect is a pairing failure because replaying the code to another authority
is unsafe. The candidate is never logged, displayed, placed in exception text,
written to temporary storage, or handed to WebView. The decoder copies the
token directly from a bounded response into a short-lived sensitive value used
only by verify/encrypt; cancellation or any claim/verification/storage failure
clears all owned mutable buffers and releases references. Kotlin/JSON can leave
uncontrollable heap copies of immutable strings, so this is explicitly bounded
lifetime and best-effort clearing—not a false memory-zeroization guarantee.
The previous pairing remains intact until the encrypted replacement commits.

One shared native transport enforces the authority invariant for **every**
route: automatic HTTP and HTTPS redirects are disabled, persistent HTTP caching
is disabled, each request URL is constructed from the saved canonical origin
plus a constant route, and `Authorization` is attached only after an exact
scheme/host/port equality check. Any redirect response or authority mismatch is
returned as `unsafe endpoint`; the client never follows and never reattaches a
bearer. Tests cover claim, health, library, transcript, job POST/hydration, and
SSE rather than relying on per-call discipline.

On process start the app decrypts the pairing, reconciles the cache/index, and
renders the cached library snapshot and any validated offline artifact without
a network round trip. Health verification gates only network refresh,
download, and mutation; it never gates reading a reconciled cached artifact.
On foreground resume/manual refresh, that probe yields:

- network/Tailscale failure -> `engine unavailable`, retain pairing;
- authenticated 401 -> `pairing expired`, retain it until a replacement is
  successfully verified, but expose only the re-pair flow; and
- TLS, redirect, or canonical-host mismatch -> `unsafe endpoint`, block use and
  require endpoint correction for network actions. Previously validated cached
  artifacts remain readable unless local cache validation itself fails.

### Fixed protocol and resource limits

Milestones use deterministic limits, all covered at boundary-minus-one,
boundary, and boundary-plus-one:

| Boundary | Limit | Failure behavior |
| --- | ---: | --- |
| Pairing request | engine's existing 4 KiB body cap | local input rejection; no claim |
| Pairing response | 8 KiB | discard candidate; prior pairing stays |
| Connect timeout | 10 seconds | engine unavailable; no automatic retry |
| Ordinary API call | 30 seconds total | retain local state; offer retry |
| Artifact download | 16 MiB, 120 seconds | retain prior artifact; explain too large/timeout |
| Library response | 4 MiB and 10,000 entries | reject whole refresh; retain prior snapshot |
| Offline cache | 256 MiB and 200 artifacts | LRU-evict unpinned entries before commit |
| Shared text | 8,192 Unicode code points | reject before URL parsing |
| SSE frame | 256 KiB | close stream, hydrate, then reconnect |

The 16 MiB artifact ceiling is over 500 times the repository's current
near-hour and long-form HTML fixtures (both under 32 KiB), leaving generous
growth without permitting an unbounded WebView/cache allocation. A future
fixture exceeding half the cap triggers a reviewed cap/renderer decision rather
than a silent increase. SSE reconnect delays are 1, 2, 4, 8, then 15 seconds,
with ±20% jitter and reset only after a successful hydrate plus event. Pairing
and non-idempotent mutations never retry implicitly. The sole mutation retry is
Milestone B's durable same-ID submission recovery: after successful health, the
app makes at most one automatic replay per foreground process session. Failure
retains the pending record and requires a visible manual retry; there is no
timer/background loop.

The engine bearer currently rotates with engine state repair/rotation; no
client recovery exists beyond a fresh desktop code. Every verified pairing
record gets a random local `pairing_generation`; replacing even a same-origin
credential creates a new generation. Network work created under an older
generation never runs automatically under the new one. Changing to a different
origin requires an explicit destructive switch confirmation and clears the old
origin's snapshot, artifacts, tracked jobs, and pending submissions before the
new pairing becomes active.

“Forget this computer” closes/cancels network work, deletes the encrypted
pairing and every pending submission, tracked job, library snapshot, cache
index/artifact/temp, WebView data, and the wrapping Keystore key, then verifies
the app-owned data domain empty. It cannot leave work that a later pairing can
replay.

## Decision 3: Keystore-wrapped credential storage

The only durable bearer owner on Android is a small encrypted pairing record in
app-private, no-backup storage. A non-exportable Android Keystore AES-256-GCM
key wraps a versioned payload containing `pairing_generation`, canonical origin,
and bearer. Each write uses a fresh nonce and authenticated associated data
containing the schema version and application ID; an atomic replace makes torn
writes fail closed. The key is not user-authentication-gated: the client must restore its
offline library and make an explicit foreground refresh after process death
without a second device-unlock prompt. This accepts that malware executing as
the unlocked app process can ask Keystore to decrypt; Android app sandboxing,
device lock/encryption, no background service, and the narrow bearer scope are
the compensating controls. The app does not use
Block Store, Auto Backup, cloud sync, preferences, DataStore plaintext, logs,
analytics, crash attachments, clipboard, or WebView storage for the bearer.

If the Keystore key is missing/invalidated or authenticated decryption fails,
the record is deleted and the app returns to pairing; it never attempts to
salvage or echo ciphertext. Android documents that Keystore keys remain
non-exportable and can constrain cryptographic use
([Android Keystore](https://developer.android.com/privacy-and-security/keystore)).

The endpoint is stored inside the same authenticated ciphertext. Nonsecret UI
preferences (theme, cache limit) may use DataStore separately. The app opts
out of Android backup for the whole credential/cache domain and defines explicit
backup exclusion rules as defense in depth.

## Decision 4: native shell, engine-rendered reader

Jetpack Compose owns connection, pairing, library, download state, empty/error
states, and later jobs. The library is fetched from bearer-authenticated
`/v1/library` but projected immediately into the minimum Android model:
`source_id`, title, and creation time. Source URLs, engine paths, and internal
job fields are neither displayed nor retained by Milestone A.

Opening a reader performs a native authenticated GET of
`/v1/transcripts/{source_id}.html`. Before caching, the repository validates:

- `source_id` is exactly 64 lowercase hex characters;
- status is 200, MIME type is HTML, and the response is within a fixed artifact
  byte cap;
- the final response authority is the saved origin (redirects are disabled);
  and
- the body is complete before an atomic cache replace.

The cache stores the exact response bytes in app-private `noBackupFilesDir`,
keyed by source ID, plus a nonsecret versioned index (title, fetch time, byte
count, SHA-256, and last access). A separate bounded library snapshot contains only
`source_id`, title, and creation time for the latest successful refresh; cached
artifact entries remain visible in Downloads even if a later library snapshot
omits them. No deletion is inferred from an incomplete/failed refresh.

Cache mutation is journal-free but ordered and reconciled. Artifact filenames
are versioned/content-addressed as `{source_id}-{sha256}.html`. A download writes
and fsyncs a same-directory temporary, atomically renames it to the new version,
then atomically switches the index from the old version to the new one. Only
after the index commit does garbage collection delete the old unpinned version.
A crash before the switch therefore leaves the old artifact readable and the
new version removable as an orphan; a crash after it leaves the new artifact
authoritative and the old version harmlessly collectible.

Removal first drops the index entry, then deletes bytes. Startup removes all
temps and orphan versions, drops index entries whose filename/size/hash/source
ID do not validate, recomputes totals, and atomically repairs the index before
display. Downloads and open readers pin a concrete version against eviction/
removal; removal becomes pending until the final handle closes. LRU eviction
applies the same index-first deletion. Fault-injection tests crash at every
file/index boundary—including both sides of a refresh switch—and race refresh,
eviction, removal, and an open WebView. They assert the old artifact remains
readable until the index commit. Refresh only replaces a known-good prior
artifact after a complete validated response, so offline reading survives
engine outage or process death. “Forget” closes Reader, deletes index/bytes/
snapshot, and verifies the directory empty. No engine path, source URL, bearer,
response header, raw library/job body, or job payload is cached.

The Reader WebView serves those exact cached bytes through an
`androidx.webkit.WebViewAssetLoader`-style application-owned HTTPS origin such
as `https://appassets.androidplatform.net/transcripts/<source_id>.html`. It
never loads the bearer-authenticated engine URL. This gives online and offline
reading one code path and keeps the bearer out of WebView requests, history,
cookies, JavaScript, and renderer markup.

The WebView containment policy is mechanical:

- JavaScript is enabled because the canonical artifact's chapter/rail behavior
  requires its engine-authored scripts; no native JavaScript bridge,
  `addJavascriptInterface`, message channel, file chooser, geolocation,
  downloads, popups, or multiple windows exists.
- file/content access and universal/file-URL access are false; no `file://` or
  `data:` top-level document is used. Android explicitly recommends an HTTPS
  asset loader over file access
  ([WebView file security](https://developer.android.com/privacy-and-security/risks/webview-unsafe-file-inclusion)).
- the request interceptor serves only the exact local transcript path and
  returns a fail-closed 404 for every other appassets resource. HTTP(S), file,
  content, intent, and custom-scheme subresource requests are blocked. Current
  artifacts already use local/system fallbacks; older cached artifacts that
  still contain the obsolete Google Fonts import select the same fallbacks
  because this interceptor blocks that request without rewriting their bytes.
- top-level navigations away from the one source-ID URL are cancelled. User
  links are offered to the platform browser only after explicit `http`/`https`
  parsing; all other schemes are rejected. No referrer or bearer is supplied.
- Safe Browsing stays enabled, debugging is disabled in release, screenshots
  are allowed because this is reader content, and WebView cookies/cache/history
  are cleared on forget even though the design creates none.

The cached document receives a local response CSP generated from the checked-in,
versioned script-shape inventory emitted by `html.py`, with every network
directive set to `none`. That inventory includes the historical rail/media-sync
texts plus the V2 rail/media-sync and local-search text introduced by #88. A
Python parity test derives the exact ordered tuples and body-tail placement; the
Kotlin parser mirrors both and refuses the whole document when text, count,
order, or placement is unknown. It never hashes arbitrary discovered script
text. This is the engine helper's fail-closed rule expressed at the Kotlin
boundary, preserves `html.py` as the single renderer, and prevents a compromised
cache document from reaching the network or native APIs.

### Future media-element authentication decision

Milestones A and B are transcript-only. If native media is later authorized,
WebView still will not receive a bearer or query token. A narrow appassets path
handler will proxy Range requests through the native authenticated HTTP client
to `/v1/media/{source_id}`, returning bytes/status/content-range to the media
element. It will accept only the currently opened validated source ID, forbid
redirects, and close when the reader closes. This is the native equivalent of
the browser surface's scoped-cookie media decision and avoids secrets in URLs.
It requires a separate threat review before implementation.

## Decision 5: Kotlin boundary types are a checked mirror

The engine remains the protocol source of truth. The Android module defines
small `kotlinx.serialization` DTOs only for routes it consumes and maps them to
domain models at the repository boundary. Decoders ignore unknown fields for
forward-compatible additive changes, but missing required fields, invalid
enums, malformed IDs, non-finite timestamps, and unexpected top-level shapes
fail closed with a self-authored compatibility message.

Contract tests use checked-in JSON fixtures emitted by the real FastAPI app for
health, pairing, library, job creation/hydration, and every SSE event kind the
client handles. A Python parity test regenerates those responses from route
models and fails on fixture drift; Kotlin tests must decode the same fixtures.
This follows the existing shared TypeScript mirror without coupling the Android
build to Python code generation or making OpenAPI generation a release-time
dependency.

All HTTP failures cross one redacting error mapper. It reports only a fixed
category, HTTP status where useful, and a request correlation ID created by the
client. It never incorporates response bodies, request JSON, URLs containing
user content, headers, exception messages from the HTTP library, or transcript
bytes into user-visible errors/logs.

## Milestone A screens and lifecycle

Milestone A has four simple Compose destinations:

1. **Connect:** explain the Tailscale prerequisite; accept Serve origin and
   pairing code; claim/verify/store.
2. **Library:** show cached content immediately, then refresh from the engine;
   distinguish offline, engine unavailable, and re-pair required.
3. **Downloads:** show the bounded offline set and allow explicit removal.
4. **Reader:** host the contained WebView and expose refresh/remove actions
   outside the document. There is no “open transcript in browser” action: the
   safe artifact URL is app-local and the engine URL requires a bearer. Explicit
   external links inside the artifact follow the separately gated browser flow.

The pairing code uses a dedicated secret input: masked by default, excluded
from saved instance state/`rememberSaveable`, Autofill and content capture,
selection/copy, accessibility value text, and IME personalized learning where
the platform supports it. It clears on submit, background, navigation, or
failure. The Connect activity sets `FLAG_SECURE` so screenshots and recents
previews cannot capture the origin/code; the flag is removed after a verified
pairing so Reader screenshots remain available. Tests assert these properties
before running the K4 UI sweep rather than expecting a user-visible input to be
magically absent while typing.

The app is useful without continuous connectivity. Process death reconstructs
state from the encrypted pairing record and cache index. Repository calls are
structured coroutines scoped to the screen/use case; navigation cancels work.
Library refresh is foreground-only in Milestone A. No permanent service,
wakelock, notification permission, broad storage permission, VPN permission,
or Tailscale SDK is added. The separately installed Tailscale Android client
owns network membership.

## Milestone B: share target, jobs, and progress

Milestone B adds an exported activity with an intent filter for exactly
`ACTION_SEND` + `text/plain`, then repeats admission checks at runtime: action
and MIME must equal those literals; `data`, `ClipData`, stream/parcelable
extras, URI-grant flags, categories beyond `DEFAULT`, and multiple URLs are
rejected. It reads only `Intent.EXTRA_TEXT`, applies the 8,192-code-point cap,
and parses exactly one `https` URL. The URI must have no userinfo, control
characters, fragment, or IDNA/authority ambiguity; its canonical full URL and
Unicode/punycode authority are shown on a confirmation sheet before mutation.
That sheet is non-saveable and uses `FLAG_SECURE`; background/navigation clears
it unless a pending encrypted record has already committed.
The engine remains responsible for supported-source validation. The activity
does not become a generic `VIEW` handler and accepts no file/content URI.
Android's documented receiving contract uses an exported intent filter plus
`ACTION_SEND`, `EXTRA_TEXT`, and the specific `text/plain` MIME type
([Android receiving shared data](https://developer.android.com/training/sharing/receive)).

Before Android submission, Milestone B adds an idempotency increment to the
engine protocol: `JobSubmission` accepts a client-generated UUIDv4
`client_request_id`. The store durably indexes it with the job. Repeating the
same ID and byte-identical normalized submission returns the original job and
never enqueues twice; reusing it for different content returns self-authored
409. Validation, persistence, and enqueue occur in one store critical section,
and existing clients that omit it retain current behavior. Route/model,
restart, concurrency, and response-lost/retry tests land before the Android
share client.

After visible confirmation, Android generates the ID and atomically stores an
encrypted pending record `{pairing_generation, canonical_origin,
client_request_id, source}` under the same Keystore-wrapped/no-backup discipline
as pairing, then posts `/v1/jobs` with
`requires_confirmation: false`. The native confirmation is the user-intent
gate, matching the installed extension. A lost response or process death marks
that exact pending request recoverable. It may replay automatically only when
both pairing generation and canonical origin exactly match the active verified
pairing. After the next successful foreground health probe, the app replays it
once for that process session; subsequent failure requires the user's Retry
action. A credential replacement makes the pending item “needs confirmation”
even at the same origin: the user must review source and destination again or
delete it before the same ID may be sent. A different-origin switch deletes it
as part of the confirmed switch; it is never offered to the new engine.

A successful response is hydrated and persisted before the pending source is
deleted. Cancel may delete the record only before POST dispatch; after dispatch
it survives UI/coroutine cancellation until idempotent replay resolves the
outcome. The durable
tracked-job record then contains only job ID, client request ID, self-authored
display title when available, submission time, and terminal-notified state—
never the source URL or origin—and is bound to the opaque pairing generation and
bounded to the most recent 20 records. The encrypted pairing record remains the
only durable owner of the canonical endpoint.

After same-origin credential replacement, old-generation tracked jobs become
dormant: the client performs no job GET, SSE open/correlation, or notification
for them. A visible “Resume tracking on this computer” confirmation lists only
self-authored titles/ages and, after the new pairing is verified, atomically
rebinds the selected records to the new generation before hydration. Decline
deletes them. A different-origin switch and Forget delete them without offering
adoption. Tests cover same-origin re-pair, different-origin switch, and Forget
and prove that no POST, hydration GET, or SSE correlation crosses generations
without the applicable explicit confirmation.

Progress uses hydrate-before-stream:

1. select only tracked records bound to the active pairing generation, then GET
   each `/v1/jobs/{id}` as source of truth;
2. open one bearer-authenticated `/v1/events` stream;
3. parse bounded `data:` frames and update only recognized job IDs/events;
4. on gap, malformed frame, process death, or reconnect, discard stream-derived
   assumptions and hydrate again before reopening with exponential backoff.

SSE runs only while a relevant foreground UI is visible. A later background
completion feature would use WorkManager polling with an explicit notification
design; Milestone B does not keep an unbounded stream alive in the background.
401 enters re-pair-required state, network loss retains pairing and jobs, and
unknown event kinds are ignored after bounded parsing. Terminal notification is
at-most-once according to the persisted flag if notifications are later added.

## K4 secret and privacy gates

The Android surface extends the existing K4 prefix-and-full-secret sweep. Tests
seed a distinctive engine bearer, pairing code, and a shared HTTPS URL carrying
a unique secret-like path/query marker into success and adversarial paths, then
inspect:

- Logcat captured from unit, instrumentation, pairing, network, WebView, share,
  process-death, and crash-handler paths;
- Compose semantics, screenshots, accessibility text, toasts/snackbars, saved
  instance state, intents, notifications, and clipboard;
- request URLs, redirect history, WebView history/cookies/storage/cache, HTTP
  caches, and mock-server logs (authorization is allowlisted in request memory
  only and the harness redacts before recording);
- app files, databases, preferences/DataStore, cache/no-backup directories,
  backup manifests, test reports, and build artifacts; and
- exception messages and serialized test failures after injected malformed
  responses, TLS errors, cancellation, disk-full, and Keystore invalidation.

The encrypted pairing file is the one durable bearer owner, but a raw filesystem
scan must find zero full or prefix matches because the bytes are ciphertext.
The test decrypts it through the production repository only to prove round-trip
and then verifies deletion on forget/invalidation. No pairing-code copy is
durable; while the Connect field is focused, UI inspection asserts masking and
the exclusion controls above rather than asserting the actively typed value is
absent from process memory. After submit/background it must be absent from UI,
saved state, files, and diagnostic surfaces. Prefix sweeps catch truncation and
partial echoes. Before engine acceptance, the encrypted pending-submission file
is the shared URL marker's sole permitted durable Android owner; ciphertext must
not match its plaintext/prefix. Sweeps cover the incoming Intent, confirmation,
saved state, UI/recents, logs/errors, cancellation before/after dispatch,
process-death replay, success, user cancel, same-origin re-pair,
different-origin switch, Forget, backup rules, and test reports. After success
or deletion, the app domain contains no marker copy; the engine-owned job is
outside this client-side allowlist. While the confirmation sheet is actively
visible, its semantics may contain the canonical URL by design; the test asserts
`FLAG_SECURE`, no saved-state/recents copy, and removal immediately on confirm,
cancel, navigation, or background rather than claiming the visible source does
not exist.

Release logging uses a compile-time redacting HTTP logger or none; debug builds
do not gain body/header logging. Crash reporting/analytics SDKs are absent from
these milestones. Android's security guidance recommends Keystore-backed
encrypted secret storage rather than source, logs, or ordinary storage
([Android security checklist](https://developer.android.com/privacy-and-security/security-tips)).

## Verification and honest proof boundary

### Linux CI gates

- Gradle wrapper verification, dependency lockfiles, reproducible lint, Kotlin
  compile, unit tests, Android lint, and release APK assembly run on Linux.
- Pure JVM tests cover origin canonicalization, claim/verify/store replacement,
  redirect refusal, state transitions, DTO/SSE parsing, LRU/atomic cache rules,
  share parsing, and every redacting error category.
- Python/Kotlin contract-fixture parity fails on engine model drift.
- Robolectric/Compose tests cover screen states and process recreation without
  requiring a real tailnet.
- Emulator instrumentation covers Keystore ciphertext, invalidation/forget,
  WebViewAssetLoader exact-byte rendering, network/navigation blocking, CSP
  violations, offline reopen, and the complete K4 sweep.
- A test HTTPS server with controlled certificates and DNS exercises the real
  client against the FastAPI app/reverse-proxy shape, including redirects,
  cross-authority attempts, 401, truncated artifacts, and SSE reconnect. Test
  trust is injected into the test process/config only and cannot enter release.
- Debug/emulator APKs are signed only with a documented non-production CI key,
  labeled disposable, and never offered as the operator acceptance artifact.
  Signing-certificate continuity is a release gate below.

CI cannot prove Tailscale installation, tailnet ACL membership, MagicDNS/TLS on
the operator's tailnet, an OEM WebView implementation, or sideload/update
behavior on the operator's physical phone. Milestone A acceptance therefore
requires installing the reviewed APK on a real Android device, pairing through
the managed Serve URL, opening one artifact, disconnecting the engine/network,
and reopening it offline. Milestone B acceptance adds sharing a real URL and
observing the hydrated terminal job. The implementation PR must state this gap
plainly and link the manual evidence.

## Distribution and signing boundary

The first APK given to the operator—even for Milestone A acceptance—uses the
stable release certificate. No disposable debug install is described as the
product deliverable, because changing signers forces uninstall and destroys the
Keystore credential/offline cache.

Before that APK, a release-handoff checklist fixes the package/application ID,
generates the signing key on an operator-controlled offline workstation, creates
and verifies two operator-controlled encrypted backups, records the public
certificate and SHA-256 fingerprint in the repository, and performs signing
offline. The unsigned reproducible APK/AAB from CI plus provenance is an input;
the private key never enters source, CI, Actions artifacts, logs, or this desk's
workspace. The signed APK, its digest, certificate fingerprint, source commit,
and build-run link are published together. A clean test device proves install,
then an incremented version proves in-place update with pairing/cache retained.
Losing all key backups means future updates require uninstall/data loss, so the
checklist is a hard prerequisite, not follow-up polish. Moving signing into an
approved hardware/encrypted CI secret later is a separate security decision.

Google Play distribution, Play App Signing, account enrollment, telemetry,
automatic update infrastructure, and public listing are later forks. The safe
default is private sideloading; no money or public surface is needed to prove
the product thesis.

## Rejected alternatives

- **Wrap `/web/` as the app:** quick, but leaves no robust offline artifact
  store/share-target architecture and entrusts durable auth to WebView cookies.
- **Send native clients through `/web/api/pair/claim`:** its browser-only Origin
  and Fetch Metadata controls do not apply to native HTTP; `/v1/pair/claim` is
  the existing deliberate native boundary.
- **Store the bearer in preferences/DataStore or inject it into WebView:** both
  create avoidable durable/readable copies and enlarge the renderer trust
  boundary.
- **Load the authenticated transcript URL directly in WebView:** navigation
  headers do not safely solve subresource/media authentication, and the bearer
  can leak into WebView state. Native fetch plus a local HTTPS asset origin has
  one auditable secret owner.
- **Rebuild transcript UI in Compose:** violates `html.py` as the single renderer
  and guarantees cross-surface drift.
- **Enable arbitrary HTTPS endpoints:** conflicts with tailnet-only scope and can
  exfiltrate one-time codes after user error. A future self-hosted-server mode
  needs an explicit trust and discovery design.
- **Embed Tailscale or discover nodes:** duplicates the installed VPN client's
  responsibility and adds identity, SDK, and platform lifecycle scope.
- **Query-string or fragment credentials:** leak through URLs, history,
  screenshots, diagnostics, and referrers.
- **Keep SSE alive as a background service:** harms battery and lifecycle
  correctness; hydrate-on-open is sufficient before a separately designed
  notification feature.

## Implementation order after a future build release

1. Confirm the acceptance device meets API 28, scaffold the pinned
   Kotlin/Compose project and CI gates, and add canonical origin, globally
   redirect-refusing redacting transport, DTO fixtures, and contract parity.
2. TDD Keystore wrapping and the claim -> verify -> atomic-store state machine.
3. TDD library projection, exact-byte atomic/LRU cache, and offline repository.
4. Add the contained appassets WebView reader and its CSP/navigation/network
   instrumentation gates.
5. Build Milestone A screens and full K4 sweep; complete stable offline release
   signing/update proof before the physical tailnet acceptance APK.
6. Only after Milestone A review, add and independently land the engine job-
   submission idempotency contract.
7. Add the share target, encrypted pending submission, bounded tracked jobs,
   hydrate-before-SSE flow, K4 extensions, and Milestone B acceptance.

No step above begins from this design PR. The design must first pass systems,
security/open-code, and product review and be surfaced to podcast-reader-xo.
