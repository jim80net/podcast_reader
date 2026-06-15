# In-App Editorial Visual Uplift — Design

**Date:** 2026-06-15
**Status:** Approved brainstorm, pre-systems-review
**Author:** Jim Park, with Claude
**Review history:** v1 — approved. Direction chosen from rendered mockups: **A, Editorial /
Reader** (warm paper, serif display titles, list-led, calm palette, sparing accent). Applies
cohesively across Library / Reader / New / Settings (+ first-run Setup) in light and dark.
v2 — design-review: keep `--accent` in the brand-blue family (flagged to Jim).
v3 — Jim chose the **warm red-brown** accent and approved recoloring the app icon to match:
`--accent` is the warm family (light ≈ `#9a3b2e` deep brick, dark a lighter warm terracotta
that passes AA), used everywhere accent is used; the shipped app icon (`build/icon.svg` →
`icon.png`) is recolored from blue to the same warm gradient so the brand stays unified. The
editorial feel is warm paper + serif + list layout + this warm accent.

## Problem

The app's visual design is the default GitHub-Primer-ish theme (`--bg #fafafa`, `--accent
#0969da`, `system-ui` everywhere) — functional but generic, not the "premium reading
product" a Podcast *Reader* should feel like. The first-impression increment gave it a brand
identity; this increment makes the day-to-day in-app experience cohesive and premium in the
chosen editorial direction.

## Goals

- A cohesive **editorial design system** — type scale (serif display + sans UI/body), warm
  light + calm dark palettes, spacing rhythm, dividers-over-boxes, sparing accent — applied
  across every view.
- **No DOM/text restructuring:** restyle via the existing semantic classes; preserve element
  roles, headings, and visible text so the 128 e2e selector references keep passing. Where a
  string genuinely improves (rare), update the matching e2e selector in the same change.
- Light **and** dark themes both first-class (the mockup showed light; dark gets an
  equivalent calm treatment).
- Accessibility: maintain WCAG AA contrast for text/controls; visible focus states; honor
  `prefers-reduced-motion` for any added motion.
- Zero new runtime risk: no new network/CSP surface, no engine/IPC changes.

## Non-goals (out of scope)

- The transcript **artifact** styling — that's `html.py`'s output in the sandboxed iframe, a
  separate surface (touched only by its own changes); this increment styles the *app chrome
  around* it (the Reader view frame, back-link, media player container), not the artifact.
- Bundling a custom web font (see Typography); a follow-on if we want pixel-identical serif
  across all OSes.
- New features or layout/IA changes — purely visual refinement of the existing screens.

## Typography (decision: system serif, no bundled font)

- **Display/titles:** a system serif stack — `Georgia, 'Iowan Old Style', 'Times New Roman',
  serif`. Georgia ships on both target OSes (Windows + macOS; Linux is dev/CI-only and falls
  back to its default serif), so we get the editorial feel with **zero font assets, no
  `font-src`/CSP change, and no licensing**. A bundled OFL serif (e.g. Newsreader/Source
  Serif 4) for pixel-identical rendering is a noted follow-on.
- **UI + body:** keep `system-ui, sans-serif` (labels, buttons, metadata, forms).
- A small type scale as tokens (e.g. `--text-xs … --text-3xl`, line-heights) so sizes are
  consistent across views.

## Design tokens (`style.css` `:root` + dark block)

Extend the existing token set (don't fork it): warm paper light palette (`--bg` warm
off-white, `--surface` white, `--fg` near-black warm, `--muted`, hairline `--border`), a calm
dark palette, and `--accent` in the **warm red-brown family** (light ≈ `#9a3b2e`, dark a
lighter warm terracotta passing AA) — the shipped app icon is recolored to the same warm
gradient (v3) so brand and accent stay unified. Plus new tokens: `--serif`, type-scale sizes, spacing scale (`--space-*`), `--radius-*`, and
a restrained `--shadow-sm` for the few elevated elements. Every component reads tokens, so
light/dark and future tweaks are one place.

## Per-view application (restyle existing classes)

- **Library** — the home: list-led rows with hairline dividers (the mockup), serif titles,
  muted metadata (source · duration · chapter/speaker tags), right-aligned date; the existing
  `.cards`/`.card`/`.card-title`/`.card-source`/`.card-date` classes restyled (kept as the
  same elements). The branded empty state restyled to match.
- **Reader** — the app chrome around the artifact iframe: a calm header/back-link, generous
  margins, the floating media player container refined; the artifact itself unchanged.
- **New** — the URL/file submission + live job progress: editorial form styling, clear
  primary action, refined progress/confirmation rows.
- **Settings** — provider dropdown, key entry/test, whisper/model/storage fields, the custom-
  endpoint list (if present): consistent form controls, sectioning, spacing.
- **Setup (first-run)** — align the hero + sections (already polished) to the new tokens.
- **Shared chrome** — top bar/nav, buttons (`.button-*`), chips/badges, focus rings — all to
  tokens.

## Accessibility & motion

- AA contrast verified for body text, muted text, and controls on both palettes.
- Visible keyboard focus on all interactive elements; the existing textContent-only DOM rule
  (eslint innerHTML fence) is preserved.
- Any hover/transition is subtle and wrapped in `@media (prefers-reduced-motion: reduce)`.

## Testing

- **Visual self-check:** re-render each view (the mockup pipeline: a static HTML repro per
  view, or screenshot the built app via Playwright) in light + dark before/after.
- **e2e:** the full Playwright suite stays green — selectors are role/text-based and the DOM
  text/roles are preserved; update only the few selectors tied to any intentionally-changed
  string. No new e2e required beyond keeping the suite green.
- **vitest + typecheck + lint:** unchanged green (this is CSS + minimal class tweaks; pure
  view logic is untouched).
- **build:** electron-vite build green; no new assets beyond CSS.

## Risks & mitigations

| Risk | Mitigation |
|------|-----------|
| Visual change breaks e2e selectors | restyle only — preserve element roles/headings/text; update a selector only if its string intentionally changes |
| Serif differs across OSes | Georgia is on both ship targets (Win+mac); calm fallback elsewhere; bundled font is a noted follow-on |
| Dark mode regresses | both palettes are first-class tokens; verify each view in dark |
| Contrast/a11y regression | AA contrast checked on both palettes; visible focus retained |
| Accidentally restyling the sandboxed artifact | out of scope — only the app chrome around the Reader iframe is touched, never `html.py` output |

## Follow-ons (tracked, not built here)

1. Bundle an OFL serif for pixel-identical typography across all OSes.
2. Richer installer artwork (NSIS header/sidebar, dmg background — from the first-impression backlog).
3. Signing/notarization (credentialed — Jim).
