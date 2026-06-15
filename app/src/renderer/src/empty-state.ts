import { hrefFor } from './router'

/**
 * Branded library empty-state content (native-app-first-impression, app-views
 * delta). Pure data so the copy and — load-bearingly — the CTA's route target
 * are unit-testable without a DOM; `views/library.ts` renders it via `el()`.
 * The mark is the play glyph from the app icon, drawn in text so the empty
 * state needs no image asset wired into the renderer bundle.
 */

export interface EmptyLibraryState {
  /** A glyph echoing the app's play mark (decorative, aria-hidden). */
  mark: string
  title: string
  lead: string
  cta: { label: string; href: string }
}

export function emptyLibraryState(): EmptyLibraryState {
  return {
    mark: '▶',
    title: 'Your library is empty',
    lead: 'Turn a podcast, YouTube video, or audio file into a clean, readable transcript.',
    cta: { label: 'Transcribe your first episode', href: hrefFor({ view: 'new' }) }
  }
}
