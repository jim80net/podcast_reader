import './style.css'

import { el } from './dom'
import { hrefFor, navigate, onRouteChange, parseHash } from './router'
import { AppStore } from './store'
import { mountLibrary } from './views/library'
import { mountNew } from './views/new'
import { mountReader } from './views/reader'
import { mountSettings } from './views/settings'
import type { Route } from './router'
import type { ViewCleanup } from './store'
import type { EngineStatus } from '../../shared/ipc'

/**
 * Renderer shell (task 4.1): hash router between the four views, an
 * engine-status indicator with failure diagnostics, and the IPC push wiring —
 * job records hydrate the store (source of truth), forwarded SSE events patch
 * it, and a validated protocol request focuses the New view (task 5.2).
 */

const store = new AppStore()

// ---- static chrome -----------------------------------------------------------

const navLinks = new Map<Route['view'], HTMLAnchorElement>([
  ['library', el('a', { text: 'Library', attrs: { href: hrefFor({ view: 'library' }) } })],
  ['new', el('a', { text: 'New', attrs: { href: hrefFor({ view: 'new' }) } })],
  ['settings', el('a', { text: 'Settings', attrs: { href: hrefFor({ view: 'settings' }) } })]
])

const enginePill = el('span', {
  class: 'engine-pill',
  text: 'engine starting…',
  attrs: { 'data-state': 'starting', role: 'status' }
})
const engineBanner = el('div', { class: 'banner error-banner', attrs: { role: 'alert' } })
engineBanner.hidden = true

const viewContainer = el('main', { class: 'view', attrs: { id: 'view' } })

const root = document.getElementById('app')
if (root === null) throw new Error('renderer bootstrap: #app missing from index.html')
root.append(
  el(
    'header',
    { class: 'app-header' },
    el('span', { class: 'app-name', text: 'Podcast Reader' }),
    el('nav', { class: 'app-nav', attrs: { 'aria-label': 'Views' } }, ...navLinks.values()),
    enginePill
  ),
  engineBanner,
  viewContainer
)

// ---- engine status indicator ---------------------------------------------------

function renderEngineStatus(status: EngineStatus): void {
  enginePill.dataset['state'] = status.state
  engineBanner.hidden = true
  switch (status.state) {
    case 'starting':
      enginePill.textContent = 'engine starting…'
      break
    case 'ready':
      enginePill.textContent = `engine v${status.version}${status.adopted ? ' (adopted)' : ''}`
      break
    case 'failed':
      enginePill.textContent = 'engine failed'
      engineBanner.textContent = `Engine failed to start: ${status.message}`
      engineBanner.hidden = false
      break
    case 'stopped':
      enginePill.textContent = 'engine stopped'
      break
  }
}

// ---- IPC push wiring (design decision 4) ----------------------------------------

window.api.onEngineStatus((status) => {
  store.setEngine(status)
  renderEngineStatus(status)
})
window.api.onJobsHydrated((jobs) => store.hydrate(jobs))
window.api.onPipelineEvent((event) => {
  const needsHydration = store.applyEvent(event)
  // An event for a job we don't know: the records are the truth — re-fetch.
  if (needsHydration) {
    void window.api
      .listJobs()
      .then((jobs) => store.hydrate(jobs))
      .catch(() => undefined)
  }
})
window.api.onProtocolRequest((job) => {
  // Protocol arrivals surface on the New view for explicit Run/Dismiss
  // (app-shell spec: nothing protocol-initiated ever auto-executes).
  store.upsert(job)
  navigate({ view: 'new' })
})

// Catch up on state broadcast before this script attached.
void window.api.getEngineStatus().then((status) => {
  store.setEngine(status)
  renderEngineStatus(status)
})
void window.api
  .listJobs()
  .then((jobs) => store.hydrate(jobs))
  .catch(() => undefined) // engine not ready yet — the hydration push follows

// ---- routing ---------------------------------------------------------------------

let cleanup: ViewCleanup | null = null

function render(route: Route): void {
  cleanup?.()
  viewContainer.replaceChildren()
  for (const [view, link] of navLinks) {
    if (view === route.view) link.setAttribute('aria-current', 'page')
    else link.removeAttribute('aria-current')
  }
  switch (route.view) {
    case 'library':
      cleanup = mountLibrary(viewContainer)
      break
    case 'reader':
      cleanup = mountReader(viewContainer, route.sourceId)
      break
    case 'new':
      cleanup = mountNew(viewContainer, store)
      break
    case 'settings':
      cleanup = mountSettings(viewContainer)
      break
  }
}

onRouteChange(render)
render(parseHash(window.location.hash))
