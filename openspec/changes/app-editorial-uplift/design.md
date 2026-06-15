# In-App Editorial Visual Uplift — Design

Full design (tokens, typography decision, per-view application, a11y, risks) is in
`docs/superpowers/specs/2026-06-15-app-editorial-uplift-design.md` (v2). Key points:

- **Direction:** Editorial / Reader (chosen from rendered mockups) — warm paper, serif
  display titles, list-led Library, dividers-over-boxes, sparing accent.
- **Typography:** system serif stack (`Georgia, …, serif`) for display + `system-ui` for
  UI/body. No bundled font → no `font-src`/CSP change, no licensing. Georgia ships on both
  target OSes (Windows + macOS); Linux is dev/CI-only.
- **Accent:** stays brand-blue (matches the shipped app icon); the mockup's warm logo color
  is not adopted as the accent.
- **Hard constraint:** restyle existing semantic classes — preserve DOM roles/headings/text
  so the existing Playwright selectors keep passing.
- **Out of scope:** the `html.py` transcript artifact in the sandboxed iframe (only the app
  chrome around the Reader is restyled).
- **A11y:** AA contrast on both palettes, visible focus, `prefers-reduced-motion`.
