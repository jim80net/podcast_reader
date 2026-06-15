# In-App Editorial Visual Uplift

## Why

The app's in-app visual design is the default GitHub-Primer-ish theme (`system-ui`, `--accent #0969da`, plain `--bg`) — functional but generic, not the premium reading experience a Podcast *Reader* should feel like. The first-impression increment gave the app a brand identity; this makes the day-to-day views cohesive and premium in the chosen **Editorial / Reader** direction (selected from rendered mockups).

## What Changes

- An **editorial design system** in `style.css`: a type scale (system **serif** display titles + `system-ui` UI/body — no bundled font, so no `font-src`/CSP change and no licensing), warm light + calm dark palettes, spacing/radius tokens, dividers-over-boxes, and sparing accent. `--accent` is a single **warm red-brown** (light `#9a3b2e` / dark `#e0876f`, both AA); the shipped app icon is recolored to the same warm gradient so brand + accent stay unified.
- Every view restyled to the system via its **existing semantic classes** — Library (list-led rows with serif titles + muted metadata + right-aligned date), Reader (calm app chrome around the unchanged artifact iframe), New (editorial form + progress), Settings (consistent form controls + sectioning), first-run Setup (aligned to the tokens), and shared chrome (top bar/nav, buttons, chips, focus rings).
- **No DOM/text restructuring:** element roles, headings, and visible text are preserved so the existing Playwright selectors keep passing; a selector is updated only if a string is intentionally changed.
- Light **and** dark first-class; AA contrast, visible focus, `prefers-reduced-motion` honored.

The transcript artifact (the `html.py` output inside the sandboxed Reader iframe) is **out of scope** — only the app chrome around it changes. No engine/IPC/network/CSP changes.

## Capabilities

### Modified Capabilities

- `app-views`: the four views (+ first-run Setup) present a cohesive editorial visual design (light + dark, AA contrast) rather than the default theme — restyled via existing classes, DOM semantics preserved.

## Impact

- **Code:** `app/src/renderer/src/style.css` (the design-system overhaul) + small per-view class/markup tweaks in `views/*.ts` where needed (no logic change). Possibly `empty-state.ts`/shared component styling. No main-process or shared-types changes.
- **Tests:** the Playwright suite stays green (role/text selectors preserved); vitest + typecheck + lint unchanged green; electron-vite build green. Visual verification by screenshotting the built views (light + dark).
- **Risk:** visual-only; no runtime/security surface. The main hazard — e2e selector breakage — is mitigated by the restyle-don't-restructure constraint.
