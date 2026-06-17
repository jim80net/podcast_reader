import './style.css'

import { THEME_KEY, applyThemePref, getThemePref, nextThemePref } from './app-theme'
import { el } from './dom'
import { engineStatusView } from './engine-status-view'
import { createJobsHydrator } from './jobs-hydrator'
import { setupNeeded } from './packs-store'
import { hrefFor, navigate, onRouteChange, parseHash } from './router'
import { AppStore } from './store'
import { mountLibrary } from './views/library'
import { mountNew } from './views/new'
import { mountReader } from './views/reader'
import { mountSettings } from './views/settings'
import { mountSetup } from './views/setup'
import type { Route } from './router'
import type { ViewCleanup } from './store'
import type { EngineStatus, UpdateStatus } from '../../shared/ipc'

/**
 * Renderer shell (task 4.1): hash router between the four views, an
 * engine-status indicator with failure diagnostics, and the IPC push wiring —
 * job records hydrate the store (source of truth), forwarded SSE events patch
 * it, and a validated protocol request focuses the New view (task 5.2).
 */

const store = new AppStore()
// All job re-hydration goes through one gate so concurrent fetches resolving
// out of order can never regress the store to a stale snapshot.
const jobsHydrator = createJobsHydrator(() => window.api.listJobs(), store)

// ---- static chrome -----------------------------------------------------------

const navLinks = new Map<Route['view'], HTMLAnchorElement>([
  ['library', el('a', { text: 'Library', attrs: { href: hrefFor({ view: 'library' }) } })],
  ['new', el('a', { text: 'New', attrs: { href: hrefFor({ view: 'new' }) } })],
  ['settings', el('a', { text: 'Settings', attrs: { href: hrefFor({ view: 'settings' }) } })]
])

// Theme toggle: cycles System → Light → Dark, persisted (app-theme.ts). The
// warm-paper Light palette is the "white and brown" theme; Dark is the calm
// palette; System follows the OS.
const THEME_LABEL: Record<string, { glyph: string; name: string }> = {
  system: { glyph: '🖥', name: 'System' },
  light: { glyph: '☀', name: 'Light' },
  dark: { glyph: '🌙', name: 'Dark' }
}
let themePref = getThemePref()
const themeToggle = el('button', {
  class: 'theme-toggle',
  attrs: { type: 'button', title: 'Theme' }
})
function renderThemeToggle(): void {
  const { glyph, name } = THEME_LABEL[themePref] ?? { glyph: '🖥', name: 'System' }
  themeToggle.textContent = glyph
  themeToggle.setAttribute('aria-label', `Theme: ${name} (click to change)`)
  themeToggle.setAttribute('title', `Theme: ${name}`)
}
// Broadcast the resolved theme so open views (the Reader) can re-theme their
// sandboxed iframes, which can't read the document's data-theme directly.
function applyAndBroadcast(): void {
  const resolved = applyThemePref(themePref)
  window.dispatchEvent(new CustomEvent('pr-theme-change', { detail: resolved }))
}
themeToggle.addEventListener('click', () => {
  themePref = nextThemePref(themePref)
  localStorage.setItem(THEME_KEY, themePref)
  applyAndBroadcast()
  renderThemeToggle()
})
renderThemeToggle()
applyAndBroadcast()
// Follow OS changes while on System.
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
  if (themePref === 'system') applyAndBroadcast()
})

const enginePill = el('span', {
  class: 'engine-pill',
  text: 'engine starting…',
  attrs: { 'data-state': 'starting', role: 'status' }
})
const engineBanner = el('div', { class: 'banner error-banner', attrs: { role: 'alert' } })
engineBanner.hidden = true
const updateBanner = el('div', { class: 'banner update-banner', attrs: { role: 'status' } })
updateBanner.hidden = true

const viewContainer = el('main', { class: 'view', attrs: { id: 'view' } })

const root = document.getElementById('app')
if (root === null) throw new Error('renderer bootstrap: #app missing from index.html')
root.append(
  el(
    'header',
    { class: 'app-header' },
    el('span', { class: 'app-name', text: 'Podcast Reader' }),
    el('nav', { class: 'app-nav', attrs: { 'aria-label': 'Views' } }, ...navLinks.values()),
    themeToggle,
    enginePill
  ),
  engineBanner,
  updateBanner,
  viewContainer
)

// ---- engine status indicator ---------------------------------------------------

function renderEngineStatus(status: EngineStatus): void {
  enginePill.dataset['state'] = status.state
  // engineStatusView owns the exhaustive mapping (incl. the assertNever guard),
  // so a new EngineStatus member fails the build rather than rendering nothing.
  const view = engineStatusView(status)
  enginePill.textContent = view.pill
  engineBanner.replaceChildren()
  if (view.banner === null) {
    engineBanner.hidden = true
    return
  }
  engineBanner.append(el('span', { text: view.banner }))
  if (view.showRestart) {
    const restart = el('button', { text: 'Restart engine', attrs: { type: 'button' } })
    restart.addEventListener('click', () => {
      restart.disabled = true
      void window.api.engineRestart()
    })
    engineBanner.append(restart)
  }
  engineBanner.hidden = false
}

// ---- auto-update surfacing (design decision 9) -----------------------------------

function renderUpdateStatus(status: UpdateStatus): void {
  updateBanner.replaceChildren()
  updateBanner.hidden = true
  switch (status.state) {
    case 'downloading':
      updateBanner.append(el('span', { text: `Downloading update v${status.version}…` }))
      updateBanner.hidden = false
      break
    case 'ready':
    case 'deferred': {
      const install = el('button', { text: 'Restart to update', attrs: { type: 'button' } })
      install.addEventListener('click', () => {
        install.disabled = true
        void window.api.installUpdate()
      })
      updateBanner.append(
        el('span', { text: `Update v${status.version} downloaded.` }),
        install
      )
      updateBanner.hidden = false
      break
    }
    case 'installing':
      updateBanner.append(el('span', { text: `Installing update v${status.version}…` }))
      updateBanner.hidden = false
      break
    case 'error':
      updateBanner.append(el('span', { text: `Update check failed: ${status.message}` }))
      updateBanner.hidden = false
      break
    case 'disabled': // quiet — dev/unsigned posture is logged main-side
    case 'idle':
    case 'checking':
      break
  }
}

window.api.onUpdateStatus(renderUpdateStatus)
void window.api.getUpdateStatus().then(renderUpdateStatus)

// ---- IPC push wiring (design decision 4) ----------------------------------------

// ---- first-run setup wizard gate (app-setup-ui spec) ------------------------------
// Auto-open once per session, when the engine is ready, the app-side flag is
// unset, and recommended packs are missing. Failures never block the app —
// the wizard stays reachable from Settings → "Run setup again".
let setupOffered = false
function maybeOfferSetup(status: EngineStatus): void {
  if (status.state !== 'ready' || setupOffered) return
  setupOffered = true
  void (async () => {
    try {
      if (await window.api.isFirstRunComplete()) return
      const { packs } = await window.api.listPacks()
      if (setupNeeded(packs)) navigate({ view: 'setup' })
    } catch {
      // pack listing unavailable (older engine, transient failure): skip
    }
  })()
}

window.api.onEngineStatus((status) => {
  store.setEngine(status)
  renderEngineStatus(status)
  maybeOfferSetup(status)
})
window.api.onJobsHydrated((jobs) => jobsHydrator.applyPush(jobs))
window.api.onPipelineEvent((event) => {
  const needsHydration = store.applyEvent(event)
  // An event for a job we don't know: the records are the truth — re-fetch.
  if (needsHydration) void jobsHydrator.refresh()
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
  maybeOfferSetup(status)
})
void jobsHydrator.refresh() // engine not ready yet? the hydration push follows

// ---- routing ---------------------------------------------------------------------

let cleanup: ViewCleanup | null = null

function render(route: Route): void {
  cleanup?.()
  viewContainer.replaceChildren()
  // Most views read best at a column width (`.view` caps at 760px); the Reader
  // hosts a full self-laid-out artifact whose chapter-nav sidebar + key-points
  // gutter only appear past ~1200px, so it opts into full window width.
  viewContainer.className = route.view === 'reader' ? 'view view-reader' : 'view'
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
    case 'setup':
      cleanup = mountSetup(viewContainer)
      break
  }
}

onRouteChange(render)
render(parseHash(window.location.hash))
