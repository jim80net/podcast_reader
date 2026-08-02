/**
 * Color-theme preference (app shell). The design ships a warm-paper LIGHT
 * palette and a calm DARK palette. A fresh install intentionally starts Light;
 * the labelled selector lets the user explicitly choose Light, Dark, or System
 * (`prefers-color-scheme`). The choice is persisted in localStorage and applied
 * by setting `data-theme` on <html>, off which the palette in style.css keys. An
 * inline script in index.html applies the same resolution before first paint
 * (no flash); this module owns runtime changes and the system-change listener.
 */

export type ThemePref = 'system' | 'light' | 'dark'
export type ResolvedTheme = 'light' | 'dark'

export const THEME_KEY = 'pr.theme'

export function getThemePref(store: Storage = localStorage): ThemePref {
  const raw = store.getItem(THEME_KEY)
  return raw === 'light' || raw === 'dark' || raw === 'system' ? raw : 'light'
}

/** Resolve a preference to a concrete palette, consulting the OS for `system`. */
export function resolveTheme(pref: ThemePref, win: Window = window): ResolvedTheme {
  if (pref === 'light' || pref === 'dark') return pref
  return win.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

/** Apply a preference now: persist it and set the resolved palette on <html>. */
export function applyThemePref(pref: ThemePref, win: Window = window): ResolvedTheme {
  const resolved = resolveTheme(pref, win)
  win.document.documentElement.dataset['theme'] = resolved
  return resolved
}
