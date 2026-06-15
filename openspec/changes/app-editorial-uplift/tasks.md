# Tasks — In-App Editorial Visual Uplift

Restyle only (preserve DOM roles/text). From `app/`: `npm run typecheck|lint|test`,
`npm run build`, `npm run e2e` (xvfb-run -a). Prefix shell commands with `timeout`.

## 1. Design system (style.css)
- [ ] 1.1 Tokens: `--serif`, type scale, spacing/radius, `--shadow-sm`; warm light + calm
  dark palettes; `--accent` kept brand-blue (light/dark).
- [ ] 1.2 Base/typography: serif display headings, `system-ui` body/UI; AA contrast; visible
  focus rings; `@media (prefers-reduced-motion: reduce)` around any transition.

## 2. Per-view restyle (existing classes; no DOM/text restructuring)
- [ ] 2.1 Shared chrome: top bar/nav, buttons (`.button-*`), chips/badges.
- [ ] 2.2 Library: list-led rows, serif titles, muted metadata, right-aligned date; empty state.
- [ ] 2.3 Reader: calm app chrome + back-link + media-player container (artifact iframe untouched).
- [ ] 2.4 New: editorial form + job progress/confirmation.
- [ ] 2.5 Settings: form controls, sectioning, spacing.
- [ ] 2.6 Setup (first-run): align hero + sections to the tokens.

## 3. Verify
- [ ] 3.1 typecheck + lint + vitest green; electron-vite build green.
- [ ] 3.2 Playwright suite green (update only selectors tied to an intentionally-changed string).
- [ ] 3.3 Screenshot each view in light + dark (built app via Playwright) for visual review.

## 4. Docs
- [ ] 4.1 Note the design-system tokens/typography in `app/README.md` (+ CLAUDE.md row if apt).
