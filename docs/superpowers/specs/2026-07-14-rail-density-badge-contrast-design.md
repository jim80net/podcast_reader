# Rail density and section badge contrast design

## Scope

Resolve the two P3 persona-walk findings without changing the reader's broader
layout or editorial styling:

- #72: keep a near-hour keyless transcript's at-rest jump rail compact.
- #73: make INTRO/OUTRO badges legible in both themes and in both their sidebar
  and chapter-heading placements.

## Rail interval

Change `_timeline_interval` so durations through 45 minutes retain five-minute
markers and durations over 45 minutes use ten-minute markers. The exact boundary
is intentional: a 45-minute artifact still emits roughly nine stops, while the
reported 59:56 case falls from roughly twelve stops to six. This preserves the
existing guarantees that stops are never hidden, marker paragraphs and body
landmarks share one computation, and longer transcripts retain ten-minute
navigation. Capping `_timeline_markers` was rejected because it would make the
effective interval depend on duration rounding and could produce irregular,
less-predictable landmarks.

Boundary tests will cover 45:00 and the first instant above it, plus the reported
near-hour case's marker count.

## Badge palette and accessibility

Replace the translucent blue literals with two theme tokens:

| Theme | Background | Foreground | Requirement |
| --- | --- | --- | --- |
| Dark | `#31465a` | `#f2f6f8` | WCAG AA, at least 4.5:1 |
| Light | `#d8e6ed` | `#29495a` | WCAG AA, at least 4.5:1 |

Use `--section-badge-bg` and `--section-badge-text` in every cascade path:
dark values in both the default `:root` and explicit-dark scope, and light
values in both the explicit-light scope and the OS-light
`prefers-color-scheme` fallback. A scope-aware test will parse all four blocks,
so an unthemed standalone artifact follows the operating-system palette rather
than accidentally retaining the dark pair.

The same tokens drive `.nav-badge-intro`, `.nav-badge-outro`, `.badge-intro`,
and `.badge-outro`, preventing the sidebar and heading variants from drifting.
Solid fills avoid contrast changing with the surface behind the badge. The blue
section-semantic family remains distinct from the product's warm interactive
accent, so this is a contrast correction rather than a restyle.

A unit test will parse each emitted theme scope, calculate WCAG contrast for its
pair, and require the correct dark/light assignment. A selector regression test
will require both badge placements to use those tokens.

## Golden and browser proof

Regenerate all canonical HTML fixtures with `uv run python tests/regen_goldens.py`.
Add a deterministic generated 59:56 keyless fixture to that script and commit its
HTML golden. The fixtures embed the stylesheet, so the chaptered golden proves
both badge-token placements stay in exported artifacts while the new near-hour
golden proves the affected duration.

Extend the Playwright artifact-geometry suite to load the near-hour golden at
390x844 and 1280x900 in explicit dark and light themes. At rest it will assert:

- exactly six links are emitted and every link is visible inside the rail;
- the links occupy no more than six rows at 390px and no more than three rows at
  1280px, and the rail consumes less than 25% of the viewport height.

The compact row bound intentionally permits one unchanged `flex:none` link per
row; the outcome gate is height and visibility, not a responsive restyle.

Separately load the existing chaptered golden in explicit dark and light themes.
For both the sidebar and heading badge placements, assert the browser's computed
foreground/background colors match the reviewed pair. Attach clipped screenshots
of both the near-hour rail cases and chaptered badge cases to Playwright's test
output. CI will retain that output as an artifact on every run, making the visual
inspection durable without introducing pixel-sensitive screenshot comparisons.
The existing anchor-clearance cases remain unchanged. No app-shell or reader
layout restyling is in scope.
