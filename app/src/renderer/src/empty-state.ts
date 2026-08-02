import { hrefFor } from './router'

/**
 * Branded library empty-state content (native-app-first-impression, app-views
 * delta). Pure data so the copy and — load-bearingly — the CTA's route target
 * are unit-testable without a DOM; `views/library.ts` renders it via `el()`.
 * The composition stays intentionally quiet: copy, a primary action, and a
 * flat rule supplied by CSS rather than an ornamental illustration tile.
 */

export interface EmptyLibraryState {
  title: string
  lead: string
  cta: { label: string; href: string }
}

export function emptyLibraryState(): EmptyLibraryState {
  return {
    title: 'Start your library',
    lead: 'Turn a podcast, YouTube video, or audio file into a clean, readable transcript.',
    cta: { label: 'New transcript', href: hrefFor({ view: 'new' }) }
  }
}
