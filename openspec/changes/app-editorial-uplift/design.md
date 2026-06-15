# In-App Editorial Visual Uplift — Design

Full design (tokens, typography decision, per-view application, a11y, risks) is in
`docs/superpowers/specs/2026-06-15-app-editorial-uplift-design.md` (v2). Key points:

- **Direction:** Editorial / Reader (chosen from rendered mockups) — warm paper, serif
  display titles, list-led Library, dividers-over-boxes, sparing accent.
- **Typography:** system serif stack (`Georgia, …, serif`) for display + `system-ui` for
  UI/body. No bundled font → no `font-src`/CSP change, no licensing. Georgia ships on both
  target OSes (Windows + macOS); Linux is dev/CI-only.
- **Accent:** a single warm red-brown (light `#9a3b2e` / dark `#e0876f`, both AA); the
  shipped app icon is recolored to the same warm gradient so brand + accent stay unified.
- **Hard constraint:** restyle existing semantic classes — preserve DOM roles/headings/text
  so the existing Playwright selectors keep passing.
- **Out of scope:** the `html.py` transcript artifact in the sandboxed iframe (only the app
  chrome around the Reader is restyled).
- **A11y:** AA contrast on both palettes, visible focus, `prefers-reduced-motion`.
