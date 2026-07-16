import { el } from '../dom'
import { emptyLibraryState } from '../empty-state'
import { extractEngineDetail } from '../engine-error'
import { formatDate, sourceLabel } from '../job-view'
import { LatestGate } from '../latest-gate'
import { hrefFor } from '../router'
import type { ViewCleanup } from '../store'
import type { LibraryEntry, LibrarySearchResult } from '../../../shared/types'

/**
 * Library view (app-views spec): cards (title, source, date) from
 * `GET /v1/library`, refreshed on `job_done` events and on hydration so a
 * completing job appears without a restart; empty state is a branded
 * first-transcript call-to-action toward New.
 */
export function mountLibrary(container: HTMLElement): ViewCleanup {
  const status = el('p', { class: 'view-status', text: 'Loading library…' })
  const list = el('div', { class: 'cards', attrs: { role: 'list' } })
  const searchInput = el('input', {
    attrs: {
      id: 'library-search-input',
      type: 'search',
      autocomplete: 'off',
      spellcheck: 'false',
      autocorrect: 'off',
      autocapitalize: 'none',
      inputmode: 'search',
      placeholder: 'Words from any episode'
    }
  })
  const clearSearch = el('button', {
    class: 'button-secondary',
    text: 'Clear',
    attrs: { type: 'button' }
  })
  const searchStatus = el('p', {
    class: 'view-status library-search-status',
    attrs: { role: 'status', 'aria-live': 'polite' }
  })
  const search = el(
    'div',
    { class: 'field library-search', attrs: { role: 'search' } },
    el('label', { text: 'Search transcripts', attrs: { for: 'library-search-input' } }),
    el('div', { class: 'library-search-controls' }, searchInput, clearSearch),
    searchStatus
  )
  container.append(el('h2', { text: 'Library' }), search, status, list)

  let disposed = false
  // load() fires from several triggers (mount, job_done, hydration, engine
  // ready); the gate makes sure an out-of-order earlier response can never
  // overwrite the latest one.
  const gate = new LatestGate()
  const searchGate = new LatestGate()
  let entries: LibraryEntry[] = []
  let searchTimer: ReturnType<typeof setTimeout> | null = null

  async function load(): Promise<void> {
    cancelSearch()
    const isLatest = gate.next()
    try {
      const loadedEntries = await window.api.listLibrary()
      if (disposed || !isLatest()) return
      entries = loadedEntries
      if (searchInput.value.trim().length >= 2) scheduleSearch(0)
      else renderLibrary()
    } catch (err) {
      if (disposed || !isLatest()) return
      status.textContent = `Library unavailable: ${extractEngineDetail(err)}`
      status.classList.add('error-text')
    }
  }

  function renderLibrary(): void {
    status.textContent = ''
    status.classList.remove('error-text')
    list.replaceChildren()
    if (entries.length === 0) {
      const content = emptyLibraryState()
      list.append(
        el(
          'div',
          { class: 'empty-state' },
          el('div', { class: 'empty-mark', text: content.mark, attrs: { 'aria-hidden': 'true' } }),
          el('p', { class: 'empty-title', text: content.title }),
          el('p', { class: 'empty-lead', text: content.lead }),
          el('a', {
            class: 'button-link button-cta',
            text: content.cta.label,
            attrs: { href: content.cta.href }
          })
        )
      )
      return
    }
    const sorted = [...entries].sort((a, b) => b.created_at - a.created_at)
    for (const entry of sorted) {
      list.append(
        el(
          'a',
          {
            class: 'card',
            attrs: { href: hrefFor({ view: 'reader', sourceId: entry.source_id }), role: 'listitem' }
          },
          el('h3', { class: 'card-title', text: entry.title }),
          el('p', { class: 'card-source', text: sourceLabel(entry.source) }),
          el('time', { class: 'card-date', text: formatDate(entry.created_at) })
        )
      )
    }
  }

  function renderSearchResults(results: LibrarySearchResult[]): void {
    status.textContent = ''
    status.classList.remove('error-text')
    list.replaceChildren()
    if (results.length === 0) {
      list.append(el('p', { class: 'empty-search', text: 'No transcript matches.' }))
      return
    }
    for (const result of results) {
      list.append(
        el(
          'a',
          {
            class: 'card search-result',
            attrs: { href: hrefFor({ view: 'reader', sourceId: result.source_id }), role: 'listitem' }
          },
          el('h3', { class: 'card-title', text: result.title }),
          el('p', { class: 'search-result-excerpt', text: result.excerpt })
        )
      )
    }
  }

  function cancelSearch(): void {
    searchGate.next()
    if (searchTimer !== null) clearTimeout(searchTimer)
    searchTimer = null
  }

  function showSearchFailure(isLatest: () => boolean): void {
    if (disposed || !isLatest()) return
    const retry = el('button', {
      class: 'button-secondary search-retry',
      text: 'Retry',
      attrs: { type: 'button' }
    })
    retry.addEventListener('click', () => scheduleSearch(0))
    searchStatus.replaceChildren(
      document.createTextNode('Search is temporarily unavailable. '),
      retry
    )
    searchStatus.classList.add('error-text')
  }

  async function runSearch(
    query: string,
    isLatest: () => boolean,
    busyAttempts: number,
    busyStarted: number
  ): Promise<void> {
    try {
      const response = await window.api.searchLibrary(query)
      if (disposed || !isLatest()) return
      if ('busy' in response) {
        if (busyAttempts >= 2 || Date.now() - busyStarted >= 3000) {
          showSearchFailure(isLatest)
          return
        }
        searchStatus.textContent = 'Searching…'
        const elapsed = Date.now() - busyStarted
        const delay = Math.min(1000, Math.max(0, 3000 - elapsed))
        searchTimer = setTimeout(() => {
          searchTimer = null
          if (!isLatest()) return
          if (Date.now() - busyStarted >= 3000) {
            showSearchFailure(isLatest)
            return
          }
          void runSearch(query, isLatest, busyAttempts + 1, busyStarted)
        }, delay)
        return
      }
      renderSearchResults(response.results)
      const messages = [
        `${response.results.length} ${response.results.length === 1 ? 'match' : 'matches'}.`
      ]
      if (response.has_more) messages.push('Showing the first 20 matches.')
      if (response.partial) messages.push('Some transcripts could not be searched.')
      searchStatus.textContent = messages.join(' ')
      searchStatus.classList.remove('error-text')
    } catch {
      showSearchFailure(isLatest)
    }
  }

  function scheduleSearch(delay = 250): void {
    cancelSearch()
    const query = searchInput.value.trim()
    const queryLength = Array.from(query).length
    if (queryLength < 2) {
      renderLibrary()
      searchStatus.textContent = queryLength === 1 ? 'Enter at least 2 characters.' : ''
      searchStatus.classList.remove('error-text')
      return
    }
    if (queryLength > 100 || query.split(/\s+/u).length > 8) {
      renderLibrary()
      searchStatus.textContent =
        queryLength > 100
          ? 'Search is limited to 100 characters.'
          : 'Search is limited to 8 terms.'
      searchStatus.classList.remove('error-text')
      return
    }
    const isLatest = searchGate.next()
    searchStatus.textContent = 'Searching…'
    searchStatus.classList.remove('error-text')
    searchTimer = setTimeout(() => {
      searchTimer = null
      void runSearch(query, isLatest, 0, Date.now())
    }, delay)
  }

  searchInput.addEventListener('input', () => scheduleSearch())
  clearSearch.addEventListener('click', () => {
    searchInput.value = ''
    scheduleSearch(0)
    searchInput.focus()
  })

  void load()
  const unsubscribers = [
    // A finished job means a new artifact: refresh. Hydration after an SSE
    // (re)connect may have absorbed a missed job_done, so refresh then too.
    window.api.onPipelineEvent((event) => {
      if (event.kind === 'job_done') void load()
    }),
    window.api.onJobsHydrated(() => void load()),
    // The engine may not have been ready on first load; retry once it is.
    window.api.onEngineStatus((engineStatus) => {
      if (engineStatus.state === 'ready') void load()
    })
  ]

  return () => {
    disposed = true
    cancelSearch()
    for (const unsubscribe of unsubscribers) unsubscribe()
  }
}
