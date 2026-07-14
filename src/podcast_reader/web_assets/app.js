const app = document.querySelector('#app')

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
  const header = element('header', undefined, 'library-header')
  const title = element('div')
  title.append(element('p', 'Private tailnet reader', 'eyebrow'), element('h1', 'Your library'))
  header.append(title, button('Log out', logout))
  const list = element('ul', undefined, 'library')
  if (!entries.length) list.append(element('li', 'Your completed transcripts will appear here.', 'empty'))
  for (const entry of entries) {
    const item = document.createElement('li')
    const open = button(entry.title, () => showReader(entry))
    const date = new Date(entry.created_at * 1000)
    item.append(open, element('time', date.toLocaleDateString()))
    list.append(item)
  }
  replaceView(header, list)
}

async function logout() {
  await request('/web/api/logout', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: '{}'
  })
  location.replace('/web/')
}

async function loadLibrary() {
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
