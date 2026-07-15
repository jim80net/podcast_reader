const app = document.querySelector('#app')
let searchGeneration = 0
let searchController
let searchTimer

function cancelSearch() {
  searchGeneration += 1
  searchController?.abort()
  searchController = undefined
  if (searchTimer !== undefined) clearTimeout(searchTimer)
  searchTimer = undefined
}

function element(tag, text, className) {
  const node = document.createElement(tag)
  if (text !== undefined) node.textContent = text
  if (className) node.className = className
  return node
}

function button(label, onClick) {
  const node = element('button', label)
  node.type = 'button'
  node.addEventListener('click', onClick)
  return node
}

function replaceView(...nodes) {
  app.replaceChildren(...nodes)
}

function message(text) {
  return element('p', text, 'message')
}

async function request(path, options = {}) {
  return fetch(path, { cache: 'no-store', ...options })
}

function showPairing(error) {
  cancelSearch()
  const panel = element('section', undefined, 'panel pairing')
  panel.append(element('p', 'Private tailnet reader', 'eyebrow'))
  panel.append(element('h1', 'Connect this browser'))
  panel.append(message('In the desktop app, choose Connect another device and enter its six-character code.'))
  const form = document.createElement('form')
  const label = element('label', 'Pairing code')
  const input = document.createElement('input')
  input.name = 'code'
  input.autocomplete = 'one-time-code'
  input.inputMode = 'text'
  input.maxLength = 6
  input.required = true
  label.append(input)
  const submit = element('button', 'Connect')
  submit.type = 'submit'
  form.append(label, submit)
  if (error) form.append(message(error))
  form.addEventListener('submit', async (event) => {
    event.preventDefault()
    submit.disabled = true
    try {
      const claim = await request('/web/api/pair/claim', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code: input.value.trim().toUpperCase() })
      })
      if (!claim.ok) throw new Error('claim')
      const candidate = (await claim.json()).token
      const health = await request('/v1/health', {
        headers: { Authorization: `Bearer ${candidate}` }
      })
      if (!health.ok) throw new Error('verify')
      const session = await request('/web/api/session', {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${candidate}`,
          'Content-Type': 'application/json'
        },
        body: '{}'
      })
      if (!session.ok) throw new Error('session')
      location.replace('/web/')
    } catch (_) {
      showPairing('That code could not be verified. Create a new code and try again.')
    }
  })
  panel.append(form)
  replaceView(panel)
  input.focus()
}

function showReader(entry) {
  cancelSearch()
  const header = element('header', undefined, 'reader-header')
  header.append(button('← Library', loadLibrary), element('h1', entry.title))
  const frame = document.createElement('iframe')
  frame.title = entry.title
  frame.sandbox.add('allow-scripts')
  frame.src = `/web/api/transcripts/${encodeURIComponent(entry.source_id)}.html`
  frame.addEventListener('load', () => {
    const theme = matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
    frame.contentWindow?.postMessage({ ch: 'pr-theme', theme }, '*')
  })
  replaceView(header, frame)
}

function showLibrary(entries) {
  cancelSearch()
  const header = element('header', undefined, 'library-header')
  const title = element('div')
  title.append(element('p', 'Private tailnet reader', 'eyebrow'), element('h1', 'Your library'))
  header.append(title, button('Log out', logout))
  const search = element('section', undefined, 'library-search')
  const label = element('label', 'Search transcripts', 'search-label')
  label.htmlFor = 'library-search-input'
  const controls = element('div', undefined, 'search-controls')
  const input = document.createElement('input')
  input.id = 'library-search-input'
  input.type = 'search'
  input.autocomplete = 'off'
  input.spellcheck = false
  input.setAttribute('autocorrect', 'off')
  input.autocapitalize = 'none'
  input.inputMode = 'search'
  input.placeholder = 'Words from any episode'
  const clear = button('Clear', () => {
    cancelSearch()
    input.value = ''
    renderList(entries)
    status.replaceChildren()
    input.focus()
  })
  clear.className = 'search-clear'
  controls.append(input, clear)
  const status = element('p', undefined, 'search-status')
  status.setAttribute('role', 'status')
  status.setAttribute('aria-live', 'polite')
  search.append(label, controls, status)
  const list = element('ul', undefined, 'library')

  function renderList(items, searching = false) {
    list.replaceChildren()
    if (!items.length) {
      const copy = searching
        ? 'No transcript matches.'
        : 'Your completed transcripts will appear here.'
      list.append(element('li', copy, 'empty'))
      return
    }
    for (const entry of items) {
      const item = document.createElement('li')
      const open = button(entry.title, () => showReader(entry))
      if (entry.excerpt !== undefined) {
        open.replaceChildren(
          element('span', entry.title, 'result-title'),
          element('span', entry.excerpt, 'result-excerpt')
        )
      }
      if (entry.created_at !== undefined) {
        const date = new Date(entry.created_at * 1000)
        item.append(open, element('time', date.toLocaleDateString()))
      } else {
        item.append(open)
      }
      list.append(item)
    }
  }

  function showSearchFailure(query, generation) {
    if (generation !== searchGeneration) return
    status.replaceChildren(
      document.createTextNode('Search is temporarily unavailable. '),
      button('Retry', () => startSearch(query, generation))
    )
  }

  async function runSearch(query, generation, busyAttempts, busyStarted) {
    if (generation !== searchGeneration) return
    searchController = new AbortController()
    try {
      const response = await request('/web/api/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query }),
        signal: searchController.signal
      })
      if (generation !== searchGeneration) return
      if (response.status === 401) return showPairing()
      if (response.status === 429 && busyAttempts < 2 && Date.now() - busyStarted < 3000) {
        status.textContent = 'Searching…'
        const delay = Math.min(1000, Math.max(0, 3000 - (Date.now() - busyStarted)))
        searchTimer = setTimeout(
          () => {
            if (generation !== searchGeneration) return
            if (Date.now() - busyStarted >= 3000) {
              showSearchFailure(query, generation)
              return
            }
            runSearch(query, generation, busyAttempts + 1, busyStarted)
          },
          delay
        )
        return
      }
      if (!response.ok) return showSearchFailure(query, generation)
      const payload = await response.json()
      if (generation !== searchGeneration) return
      renderList(payload.results, true)
      const messages = []
      if (payload.results.length) {
        messages.push(`${payload.results.length} ${payload.results.length === 1 ? 'match' : 'matches'}.`)
      } else {
        messages.push('0 matches.')
      }
      if (payload.has_more) messages.push('Showing the first 20 matches.')
      if (payload.partial) messages.push('Some transcripts could not be searched.')
      status.textContent = messages.join(' ')
    } catch (error) {
      if (generation !== searchGeneration || error?.name === 'AbortError') return
      showSearchFailure(query, generation)
    }
  }

  function startSearch(query, generation) {
    status.textContent = 'Searching…'
    runSearch(query, generation, 0, Date.now())
  }

  input.addEventListener('input', () => {
    cancelSearch()
    const generation = searchGeneration
    const query = input.value.trim()
    if (query.length < 2) {
      renderList(entries)
      status.textContent = query.length === 1 ? 'Enter at least 2 characters.' : ''
      return
    }
    status.textContent = 'Searching…'
    searchTimer = setTimeout(() => startSearch(query, generation), 250)
  })

  renderList(entries)
  replaceView(header, search, list)
}

async function logout() {
  cancelSearch()
  await request('/web/api/logout', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: '{}'
  })
  location.replace('/web/')
}

async function loadLibrary() {
  cancelSearch()
  try {
    const response = await request('/web/api/library')
    if (response.status === 401) return showPairing()
    if (!response.ok) throw new Error('library')
    showLibrary(await response.json())
  } catch (_) {
    replaceView(message('The library is temporarily unavailable. Reload to try again.'))
  }
}

loadLibrary()
