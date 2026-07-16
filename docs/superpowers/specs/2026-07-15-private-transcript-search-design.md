# Private transcript search — 20% Day design

**Issue:** #87
**Status:** design for a bounded Demo Day vertical slice
**Spend:** none; standard-library parsing and existing browser assets only

## Outcome

A paired user can search words across completed transcripts from the private
tailnet library and open a matching episode. Podcast Reader sends search terms
only to the engine host; browser/OS input services remain outside its trust
boundary. There is no LLM, remote index, telemetry, or added dependency.

The first slice ships in the private web reader because it is the only reader
available from every tailnet device and gives Demo Day a real phone/desktop
path. The search core and response model are UI-neutral so the Electron library
can adopt them later without changing matching semantics. Desktop UI is not in
this slice.

## Boundary and protocol

Search is `POST /web/api/search`, not a query-string GET. Access logs therefore
never receive the user's search text in the URL. The route:

- passes the same exact HTTPS Origin, `Sec-Fetch-Site`, JSON content type, and
  bounded `Content-Length` gate as the existing browser session mutations;
- is bearer-exempt only at that exact method/path because the browser bearer has
  already died after session minting;
- verifies the scoped HttpOnly web-session cookie before reading library data;
- accepts `{ "query": string }` with extra fields forbidden; and
- returns a response containing at most 20 minimized results (`source_id`,
  `title`, `excerpt`) plus only two aggregate booleans: `has_more` and `partial`.

The body cap remains 4096 bytes at middleware. Application validation trims the
query and accepts 2–100 Unicode code points with at most 8 whitespace-delimited
terms. Invalid bodies use the existing redacted 422 handler; the query value is
never reflected. Empty/one-character input is handled locally by the shell and
does not make a request.

## Matching core

`engine/search.py` owns a pure, synchronous search function. It receives the
already-validated library entries, not a raw directory or source URL.

Search work is mechanically bounded. One process-wide nonblocking lock permits
one active scan; a second request gets a generic 429 with fixed `Retry-After: 1`
and no query/error detail, and cannot queue another threadpool scan. A scan
visits newest entries first and stops at the first of:

- 500 artifacts visited;
- 2 MiB for one artifact;
- 32 MiB read in aggregate;
- a 1.5 second cooperative monotonic deadline, checked between 64 KiB chunks;
  or
- 21 matches (enough to return 20 and set `has_more`).

The pure core accepts an injected clock for deterministic deadline tests. It
accepts regular files only, verifies size before opening, then reads through a
strict incremental UTF-8 decoder and feeds the parser in 64 KiB chunks, checking
the deadline and remaining byte budgets between chunks. The deadline is honestly
cooperative at that chunk granularity, not a claim that Python can interrupt a
blocked OS read. Podcast Reader's managed library is local disk; non-regular
paths are rejected. The route releases the process-wide lock unconditionally in
`finally`, including read/parser failures. It never reads beyond the per-file or
aggregate remainder and marks `partial=true` when an unreadable/invalid/oversize
artifact is skipped or any budget stops the scan. Budget tests use counting
readers and a fake clock to prove the exact ceilings and lock-release tests inject
read/parser exceptions. The response never names which artifact was skipped.
This narrows the product claim honestly: search the bounded, currently readable
portion of the completed library, and disclose when the answer is incomplete.

For each artifact within budget it uses strict UTF-8 decoding and
`html.parser.HTMLParser` to collect only:

- the document `<title>` text; and
- visible transcript paragraphs carrying `data-start`.

It ignores navigation, summaries, timestamps, scripts, styles, and metadata, so
duplicate rail/chapter labels do not inflate results and executable text cannot
become a snippet. Extraction state is new per artifact. A document is accepted
only when it has exactly one opened-and-closed `html`, `body`, `div#content`, and
descendant `main`, matching the canonical `html.py` serialization; every
captured `p[data-start]` closes before its parent; and no script/style or nested
paragraph appears inside a captured paragraph. Invalid
UTF-8, truncated/unclosed target structure, invalid nesting, parser exceptions,
and read/stat failures discard that artifact and set `partial`. Tests cover each
case, including script/style nesting inside a timestamped paragraph. One bad
cached episode never makes the whole route fail.

Matching applies Unicode NFKC normalization plus `casefold()` to query and
corpus words, with AND semantics across query terms. This handles canonically
equivalent accents and expansions such as `ß` → `ss`. Title and transcript text
both participate. Results retain newest-first visit order.

Excerpt selection never maps offsets from a length-changing normalized string
back into original text. The parser retains the original whitespace-collapsed
paragraph and its original word spans alongside normalized words. It prefers
the earliest paragraph containing every term; otherwise it selects the
paragraph containing the most distinct terms, breaking ties by document order.
The excerpt clips on original word boundaries around the earliest matched word,
to at most 180 characters, and adds leading/trailing ellipses when clipped.
Mixed title/body matches therefore show the strongest body evidence; an
all-title match uses `Matches the episode title.`. Tests pin composed/decomposed
accents, expanding folds, split-across-paragraph terms, mixed title/body terms,
ties, and word-safe clipping. The engine returns plain strings; the shell renders
every field with `textContent` only.

This is deliberately a scan, not a persisted index. It has no invalidation,
secret-bearing cache, or new lifecycle. If measured libraries later make the
hard limits visible too often, a versioned local index is a separate investment
with its own deletion and crash-consistency design.

## Shell behavior

The existing library view gains a non-form, unnamed `type=search` input above
the episode list. It sets `autocomplete=off`, `spellcheck=false`,
`autocorrect=off`, `autocapitalize=none`, and `inputmode=search`; it never restores
the query after navigation or reload. The app sends query text only to the local
engine. Browser/OS keyboards and input services remain outside the app trust
boundary, which the Demo Day privacy language states honestly.

Pairing-code typography is scoped to `.pairing input`; the search field uses
ordinary casing/spacing, an accessible visible label, a visible clear control
with at least a 44 px target, retained focus during result updates, and a
dedicated `aria-live=polite` status region. It keeps the full library list already
in memory and:

- waits 250 ms after typing before searching;
- owns one `AbortController` and generation counter, both invalidated on a newer
  query, clear, result open, library reload, logout, pairing/session expiry, and
  any other view replacement;
- treats 429 as an ordinary busy state: it remains at `Searching…` and retries
  only the current generation after the fixed `Retry-After`, at most twice and
  for at most 3 seconds total. A newer query replaces the pending generation;
  clear/view/session transitions cancel the retry timer. Exhaustion reaches the
  explicit Retry state rather than looping;
- restores the complete list when cleared; one character also restores it and
  says `Enter at least 2 characters` without a request;
- shows `Searching…`, `No transcript matches`, `Showing the first 20 matches`,
  and `Some transcripts could not be searched` according to response metadata;
- renders a failure with a real Retry control that retains the live query; a 401
  clears/removes the input and enters the existing pairing flow instead;
- labels results with title and excerpt; selecting one opens the existing reader;
  and
- preserves the current responsive layout and light/dark system themes.

The search request uses the existing same-origin `request()` wrapper. No query,
excerpt, cookie, or candidate bearer enters local/session storage, console output,
DOM attributes, URLs, or response headers. The K4 canary is allowed only in the
live input, JSON POST body, matching response/visible `textContent`, engine scan
memory, and the seeded transcript that necessarily contains it. Tests inspect
input attributes and removal plus browser form state, storage, URLs, request and
response headers, proxy/engine logs, error bodies, and uploaded test output for
any other occurrence.

## Proof and Demo Day package

TDD covers parser scoping and structural rejection, normalized Unicode AND
matching, excerpt rules, every work budget, single-flight rejection, partial/
more metadata, body validation, exact auth exemptions, and minimized response
fields. The K4 browser sweep follows the allowlist above. The real HTTPS browser
test searches a seeded transcript, opens the result, and proves unmatched,
one-character, clear/reset, visible loading, retry, stale-response, and 401 →
pairing behavior. It also pins the routine overlap path: query A is still
scanning, query B becomes current, B receives 429, automatic retry runs only B,
and B succeeds without showing an error.

Demo captures are a 390 px phone search-result state and a 1280 px desktop
search-result state in opposite system themes. Both visibly include the populated
query, useful excerpt, clear affordance, completeness status, and an openable
result. The package under
`state/demo-day-20260715/podcast-reader-build/` contains the plain-language pitch,
the two captures, the PR/run links, and a 90-second click path.

## Honest limits and next investment

The scan is intentionally suitable for today's personal libraries, not a claim
of instant search at thousands of long transcripts. Demo Day should ask for no
decision: ship the slice if gates are clean, then measure real library sizes
before proposing an index or desktop UI. Semantic search remains explicitly out
of scope and would require a separate privacy/cost design.
