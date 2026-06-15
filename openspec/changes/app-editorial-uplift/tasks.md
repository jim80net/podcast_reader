# Tasks — In-App Editorial Visual Uplift

Restyle only (preserve DOM roles/text). From `app/`: `npm run typecheck|lint|test`,
`npm run build`, `npm run e2e` (xvfb-run -a). Prefix shell commands with `timeout`.

## 1. Design system (style.css)
- [x] 1.1 Tokens: `--serif`, type scale, spacing/radius, `--shadow-sm`; warm light + calm
  dark palettes; `--accent` warm red-brown (light `#9a3b2e` / dark `#e0876f`), matching the
  recolored app icon (design v3).
- [x] 1.2 Base/typography: serif display headings, `system-ui` body/UI; AA contrast; visible
  focus rings; `@media (prefers-reduced-motion: reduce)` around any transition.

## 2. Per-view restyle (existing classes; no DOM/text restructuring)
- [x] 2.1 Shared chrome: top bar/nav, buttons (`.button-*`), chips/badges.
- [x] 2.2 Library: list-led rows, serif titles, muted metadata, right-aligned date; empty state.
- [x] 2.3 Reader: calm app chrome + back-link + media-player container (artifact iframe untouched).
- [x] 2.4 New: editorial form + job progress/confirmation.
- [x] 2.5 Settings: form controls, sectioning, spacing.
- [x] 2.6 Setup (first-run): align hero + sections to the tokens.

## 3. Verify
- [x] 3.1 typecheck + lint + vitest green; electron-vite build green.
- [x] 3.2 Playwright suite green (update only selectors tied to an intentionally-changed string).
- [x] 3.3 Screenshot each view in light + dark (built app via Playwright) for visual review.

## 4. Docs
- [x] 4.1 Note the design-system tokens/typography in `app/README.md` (+ CLAUDE.md row if apt).
