# In-transcript search — design

**Issue:** #88
**Status:** design for implementation review

## Outcome and boundary

One canonical transcript artifact can find a phrase, show the number of matching
passages, and move backward or forward between them. The feature is emitted by
`html.py`, so the same bytes work in the desktop Reader iframe, the private-web
opaque-origin iframe, a standalone file, and a future Android cached Reader.

This is local DOM search. It introduces no API, engine query, dependency,
network request, LLM, analytics, persistence, or spend. Existing stored
artifacts remain byte-valid and keep their present behavior; newly rendered
artifacts carry search. Retrofitting old stored bytes is out of scope because it
would create a second renderer or mutate the cache outside `html.py`.

## Decision 1: a passage is the match and evidence unit

Search first scans every `p[data-start]`; one noncanonical candidate fails the
whole index. The only accepted ancestry is either a direct child of
`div#content > main` (keyless) or a direct child of
`main > section.chapter-section > div.chapter-main` (chaptered), with the exact
generated timestamp attributes and no `hidden`/`aria-hidden="true"` ancestor.
Template content and CSS-hidden/noncanonical wrapper ancestry are rejected.
It builds each accepted passage's searchable string from descendant text while
excluding `.ts` and `.speaker`, so timestamps and speaker labels cannot create
false matches. Pull-quote `<strong>` text remains searchable. Chapter summaries,
titles, key points, navigation labels, footer text, and search controls are not
searched.

The trimmed query is whitespace-collapsed, limited to 100 Unicode code points,
normalized with NFKC, and lower-cased. Passage text receives the same treatment.
Matching is one literal substring, not a regular expression or token query.
That gives exact Hangul/CJK substring behavior and case-insensitive Latin text
without interpreting punctuation as code.

Every matching passage gets the shared `search-match` visual state and the
current passage also gets `search-match-active`. Highlighting the passage—not
rewriting text nodes with `<mark>`—preserves the renderer's engine-authored DOM,
pull-quote markup, media-sync listeners, selection, and copy behavior. The
existing timestamp inside the passage remains visible; the live status also
reports `current of total · timestamp`. Search and library-result states share
the existing accent/glow vocabulary: quiet accent-glow for a match and a solid
accent edge for the current match. Media playback may retain `sync-active`, but
its V2 script suppresses only automatic scrolling while a search result is
active so the two navigation owners cannot fight; playback time/highlight
updates continue.

The in-memory index is capped at 10,000 passages, 100,000 raw UTF-16 code units
per passage, 4 million raw and 4 million retained-normalized units in aggregate,
and 100,000 total visited DOM nodes (including excluded summaries/navigation).
Index construction uses iterative element/text TreeWalkers rather than an
unbounded selector or `textContent`: it rejects passage 10,001, checks each
eligible raw text-node, per-passage, and aggregate lengths before concatenation,
joins only the 100,000-unit-bounded passage, then NFKC-normalizes that whole
passage so composition
across `<strong>`/text-node boundaries is preserved. It checks the retained
normalized passage and aggregate before publishing the index. Visit 100,001,
raw cap+1, or normalized cap+1 fails atomically. An artifact above any cap gets
the fixed state `This transcript is too large to search.` and no
partial or misleading result set. The query's 100-code-point bound uses a
short-circuiting Unicode code-point iterator rather than UTF-16 `.length`. Input
work is debounced by 150 ms. These are UI responsiveness bounds, not persisted
indexing.

The initial successful index is a snapshot. Script order is rail V2, sync V2,
then search, so sync's one-time cursor initialization finishes before the search
observers register. One observer watches main child/text and relevant attributes;
another watches the exact `html > body > div#content > main` ancestor chain for
child-list reparent/removal plus `hidden`, `aria-hidden`, class, and style
visibility changes. Every result commit and navigation also revalidates exact
parent identity from passage through `main`, `div#content`, `body`, and `html`,
so even a mutation delivered between observer turns fails before scroll or
announcement. They remain
connected continuously: callbacks accept only exact harmless renderer states
(light/dark `html[data-theme]`, the layout owner's `scroll-padding-top`, base body
class plus `transcript-search-active`, passage base class plus `sync-active` /
search tokens, and fixed `aria-current`/`tabindex`). Nested class changes and any
other transition invalidate, so a queued hostile mutation cannot be lost behind
an owned disconnect. The rail subtree is excluded because its V2 script
legitimately toggles `stuck` and the layout owner writes its `top`; rail content
was already rejected as search scope during initial traversal. Every other mutation atomically clears results
and changes the fixed status to `Transcript changed; reopen it to search.` There
is no partial reindex of a moving DOM. Adversarial tests cover forged
header/control passages, hidden/template ancestry, a post-index text mutation,
cross-`<strong>` Unicode composition, raw-under-cap text whose retained NFKC form
exceeds the normalized cap, post-index body/content hiding, a queued hostile
mutation adjacent to an owned class update, and 100,001 excluded nodes. An integration test
proves sync initialization, sync highlight changes, rail stuck/unstuck, and
layout remeasurement leave the index usable.

## Decision 2: compact sticky controls coexist with the keyless rail

A renderer-emitted `role="search"` control sits after the document header and
before `<main>`. Its collapsed state is a compact `Find in transcript` button
with the `/` shortcut hint. Opening it reveals:

- a labeled `type="search"` input with no `name`, `autocomplete="off"`,
  `spellcheck="false"`, `autocorrect="off"`, and
  `autocapitalize="none"`;
- previous and next buttons;
- a clear/dismiss button; and
- a polite live status for count, current position, and bounded failures.

The opener exposes `aria-keyshortcuts="/"`, `aria-expanded`, and `aria-controls`.
`/` opens and focuses search unless the target is an input, textarea, select, or
contenteditable element. The handler ignores `defaultPrevented`, composition,
and Ctrl/Meta/Alt-modified keys. Enter moves next, Shift+Enter moves previous,
and both directions wrap; Enter/Escape during IME composition do nothing.
Previous, Next, Read passage, and Clear have explicit accessible names. Escape
and Clear remove every search class, erase the input value, collapse
the control, and return focus to its opener. Empty and no-match states disable
navigation. A completed query selects and scrolls to result 1 while focus stays
in the input; every edit resets to result 1, no-match removes the current state,
and stale work cannot scroll or announce. The current passage receives only the
fixed `aria-current="location"` marker. A `Read passage` button moves focus to
that passage via a temporary `tabindex="-1"`; cycling remains on Enter while
focus stays in the input. Dismissal removes both fixed attributes. Query state
lives only in the script closure and input value.

The search control is sticky at the top of the transcript. One V2 search-layout
updater is the sole writer of the rail's inline `top` and the document's inline
`scroll-padding-top`. It measures search plus rail when present, or search alone
for chaptered/empty artifacts. The V2 rail script owns only its stuck class and
notifies the updater after stuck changes. The updater runs after initial DOM
readiness, open/collapse, rail stuck changes, window resize, and
`ResizeObserver` changes to either element, so wrapping and font/viewport changes
cannot race or clobber geometry. Script order is rail V2, sync V2, then search;
notifications before updater registration are harmless because initial layout
always remeasures. A static fallback keeps no-JavaScript anchor jumps usable. At 390 px
the open controls use a full-width input row and a compact navigation row; no
control is below 44 CSS pixels. At desktop widths they occupy one row within the
reading column. The chapter sidebar remains unchanged.

The 390 px combined open-search plus expanded-rail stack must occupy at most 40%
of an 844 px viewport and leave at least 500 px for reading; the stuck-rail state
must be smaller. At both 390 and 1280, search and rail rectangles never overlap,
rail `top` equals measured search height, and anchor/search targets land below
the bottom of both. Playwright exercises closed→open, expanded→stuck, resize,
and text-wrap remeasurement while asserting controls neither clip nor shrink
below 44 px.

`scrollIntoView` uses smooth movement only when the user has not requested
reduced motion. The reduced-motion media query sets global
`html { scroll-behavior: auto; }` as well as removing search/sync transitions;
both search and resumed media-sync jumps use nonanimated movement. Playwright
pins this without intermediate smooth-scroll frames.

Visual states are mechanical: passive search matches keep an inset dashed
accent-dim edge plus accent-glow; the current match adds a solid accent outline;
playback keeps a distinct inset bottom edge. Combined states retain both edge
shapes, so neither state replaces the other or relies only on color. Computed
color probes pin text contrast at 4.5:1 or better and every non-text edge at 3:1
or better against its adjacent background in light and dark, analogous to #73.

## Decision 3: one inert script, checked at every serving boundary

`html.py` emits `_SEARCH_SCRIPT` for newly rendered artifacts and retains the
first-release text as byte-pinned `_SEARCH_SCRIPT_V1`. The V1 guard watched the
whole document and could fail closed on benign browser-extension decoration; it
remains authorized only in its exact historical tuples so artifacts rendered
between #90 and #92 keep working. It is never emitted into new artifacts. The
renderer also emits V2 rail and media-sync scripts for the geometry and
scroll-ownership changes above.
The script uses DOM APIs, timers, `ResizeObserver`, `matchMedia`, and event listeners
only. It contains no `fetch`, XHR, WebSocket, beacon, storage, cookie, history,
clipboard, console, dynamic script/style insertion, or cross-frame messaging.

The private web reader imports `_SEARCH_SCRIPT`, the historical compatibility
inventory, and the emitted V2 texts into `web_surface._ALLOWED_SCRIPT_TEXT`.
Allowlisting individual text is necessary but not sufficient: `transcript_csp()`
parses each script's exact text, direct parent, and body-tail slot. It emits
hashes only when scripts are direct body children in the canonical renderer slot
after the closed `div#content`, before `</body>`, and their entire ordered tuple
exactly equals one versioned shape. Otherwise its script
policy is `'none'`; it never partially blesses the known subset. Accepted tuples
come from an explicit release-history inventory: implementation inspects
committed goldens and renderer changes from the first scripted artifact onward,
preserves each historically emitted exact text under a versioned constant, and
lists every ordered tuple that was valid at that release. At minimum this covers
no-script, chapter-scroll-only, rail-only, media-sync-V1-only, rail-V1 +
media-sync-V1, and chapter-scroll + media-sync-V1 shapes. Tests cover:

- new empty artifact: media sync V2 + search V2;
- new keyless artifact: rail V2 + media sync V2 + search V2;
- new chaptered artifact: chapter scroll + media sync V2 + search V2;
- first-search-release artifacts: each exact renderer tuple with search V1;
- stored V1 empty artifact: media sync V1 only;
- stored V1 keyless/chaptered artifacts: their exact prior rail/sync hashes;
- every older tuple pinned by a historical committed golden/release fixture;
- any duplicate, reordered, unknown, modified, known-plus-unknown, or known tuple
  moved to `<head>`/inside content: no script is blessed.

The Android design's checked-script section changes from three hashes to the
same explicit versioned inventory (at minimum: unchanged chapter scroll, rail
V1/V2, media sync V1/V2, and search V1/V2) and keeps the same Kotlin parity requirement. Existing artifacts
with prior scripts remain valid; adding exact versioned constants does not
authorize arbitrary discovered text.

Standalone files have no serving CSP, so the script's inert construction is the
security boundary there. It neither creates a network-capable element nor reads
or writes a URL. Desktop and private-web iframes retain `sandbox="allow-scripts"`
without same-origin access.

## Privacy and failure discipline

Search text is private user input. It is permitted in the live input value and
ephemeral closure/timer/normalized runtime memory only while that generation is
current. It must not enter URLs/fragments, DOM
attributes, storage, cookies, logs/console, exception text, postMessage,
requests, response headers, or persisted artifact bytes. Status and errors use
fixed copy and never echo the query. Every input change, Clear, Escape, indexing
failure, and `pagehide` teardown cancels the pending timer and increments a
monotonic generation. A callback checks its generation before work, builds the
complete result list without changing classes/status, checks again, and only
then commits. Clear/dismiss erases the input and result references, so stale
work can neither repaint nor announce after dismissal. `pagehide` performs the
same teardown; a persisted `pageshow` (BFCache) and an ordinary reload always
start blank and collapsed rather than trusting `autocomplete="off"`. JavaScript cannot promise
heap zeroization, so the design makes no such claim.

The K4 browser proof uses a query canary absent from the artifact and sweeps the
URL, every DOM attribute, local/session storage, IndexedDB, Cache Storage,
cookies visible to the document, console output, and all observed requests.
Opaque-frame storage/cookie probes run independently and treat only the expected
`SecurityError` as an inaccessible-empty result so one denied API cannot abort
the remaining sweep. The
live input property is the sole explicit allowance. After clear and after Reader
teardown, the canary must be absent from both DOM properties and the full sweep.

Unexpected DOM shape, missing controls, an indexing exception, or a resource-cap
failure clears highlights, disables navigation, and shows fixed local copy. It
never falls back to a network or parent-frame search.

## Implementation and proof order

1. Add renderer structure, theme tokens, responsive geometry, and `_SEARCH_SCRIPT`
   behind failing `test_html.py` structure/contract tests and failing Playwright
   behavior tests for exact searched scope, Hangul, Latin case-insensitivity,
   keyboard/focus behavior, clear, stale debounce generations, raw/normalized
   cap+1, cross-node normalization, excluded-node visit cap, Unicode code-point
   query bounds, IME/modifier handling, opener
   disclosure semantics, BFCache/reload reset, mutation invalidation, and
   reduced motion.
2. Extend the CSP allowlist and its exact conditional-combination/unknown-script
   tests; update the Android checked-hash design note.
3. Regenerate all renderer goldens with `tests/regen_goldens.py` and keep the
   existing #72 rail-geometry and #73 contrast probes green.
4. Add Playwright proof in a standalone artifact, the private-web opaque iframe,
   and the Electron Reader. Cover `/`, Enter/Shift+Enter, Escape/Clear, Hangul,
   no results, scroll ownership, K4, and no network/storage.
5. Capture the near-hour keyless artifact at 390 and 1280 in light and dark with
   open search in both expanded-rail and stuck-rail states, showing passive and
   active matches. Add one 1280 chaptered capture/geometry assertion proving
   reading-column alignment beside the unchanged 280 px sidebar. Then run
   Python, strict mypy/Ruff, app type/lint/unit/E2E, frozen smoke, and the three
   independent diff reviews before surfacing the PR.

## Explicit non-goals

- library-wide search changes, semantic search, fuzzy matching, or regex
- a persisted/background index or query history
- changing old artifact bytes in place
- a parent-shell search implementation
- search inside summaries, chapter labels, key points, or metadata
- Android implementation, media authentication, or any public surface
