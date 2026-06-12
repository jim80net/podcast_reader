/**
 * Hash-based routing for the four views (design decision 1: vanilla TS, no
 * framework). Routes are plain `#/view` fragments so `<a href>` navigation is
 * native and keyboard-accessible; anything unparseable lands on Library.
 */

export type Route =
  | { view: 'library' }
  | { view: 'reader'; sourceId: string }
  | { view: 'new' }
  | { view: 'settings' }
  | { view: 'setup' }

export function parseHash(hash: string): Route {
  const path = hash.replace(/^#\/?/, '')
  if (path === 'new') return { view: 'new' }
  if (path === 'settings') return { view: 'settings' }
  if (path === 'setup') return { view: 'setup' }
  const readerMatch = /^reader\/(.+)$/.exec(path)
  if (readerMatch !== null && readerMatch[1] !== undefined) {
    try {
      return { view: 'reader', sourceId: decodeURIComponent(readerMatch[1]) }
    } catch {
      return { view: 'library' } // malformed percent-escape
    }
  }
  return { view: 'library' }
}

export function hrefFor(route: Route): string {
  switch (route.view) {
    case 'library':
      return '#/library'
    case 'new':
      return '#/new'
    case 'settings':
      return '#/settings'
    case 'setup':
      return '#/setup'
    case 'reader':
      return `#/reader/${encodeURIComponent(route.sourceId)}`
  }
}

/** Subscribe to hash changes; returns an unsubscribe function. */
export function onRouteChange(listener: (route: Route) => void): () => void {
  const handler = (): void => listener(parseHash(window.location.hash))
  window.addEventListener('hashchange', handler)
  return () => window.removeEventListener('hashchange', handler)
}

export function navigate(route: Route): void {
  window.location.hash = hrefFor(route)
}
