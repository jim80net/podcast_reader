import { captureTarget } from './capture'
import { claimToken, EngineClient, EngineRequestError } from './client'
import { probeConnection } from './connection'
import { el } from './dom'
import { applyEvent, isTerminal, viewFromRecord } from './jobs-view'
import { serializeNetscape, uniqueCookies } from './netscape'
import { performPairing, resolvePairingInput } from './pairing'
import { SseParser } from './sse'
import { localStore } from './storage'
import { trackSubmission } from './tracking'
import type { JobView } from './jobs-view'
import type { Pairing } from './storage'
import type { SourceKind } from './url-detect'
import { classifySource, sourceLabel } from './url-detect'
import type { JobRecord, PipelineEvent } from '../../app/src/shared/types'

/**
 * The popup: submission surface (per U1 — with `default_popup` set,
 * `action.onClicked` never fires; the toolbar click opens this page and
 * grants `activeTab`), pairing form, live progress (hydrate-then-stream,
 * popup-lifetime only), and the cookie-capture flow for
 * `download_auth_required` failures. Every engine-supplied or page-derived
 * string reaches the DOM via `textContent` (per U7 — dom.ts is the only
 * construction path, fenced by eslint).
 */

const store = localStore()
const root = (): HTMLElement => {
  const node = document.getElementById('app')
  if (node === null) throw new Error('popup root missing')
  return node
}

let streamAbort: AbortController | null = null
let views = new Map<string, JobView>()

void init()

async function init(): Promise<void> {
  const pairing = await store.pairing()
  const state = await probeConnection(pairing, (p) => new EngineClient(p).health())
  const container = root()
  container.replaceChildren()
  if (state.state === 'unpaired') {
    renderPairingForm(container, 'Pair with the Podcast Reader desktop app to get started.')
    return
  }
  if (state.state === 'unauthorized') {
    renderPairingForm(
      container,
      'Pairing expired — the desktop app issued a new token. Mint a fresh code in Settings and pair again.'
    )
    return
  }
  if (state.state === 'engine-down') {
    renderEngineDown(container)
    return
  }
  await renderConnected(container, state.pairing)
}

// ---- pairing -------------------------------------------------------------------

function renderPairingForm(container: HTMLElement, intro: string): void {
  const combinedInput = el('input', {
    attrs: { type: 'text', placeholder: 'Paste the pairing code (e.g. 51234-ABC234)', id: 'pair-combined' }
  })
  const portInput = el('input', { attrs: { type: 'text', placeholder: 'Port', id: 'pair-port' } })
  const codeInput = el('input', { attrs: { type: 'text', placeholder: 'Code', id: 'pair-code' } })
  const pairButton = el('button', { text: 'Pair', attrs: { type: 'submit', id: 'pair-submit' } })
  const error = el('p', { class: 'error-text', attrs: { role: 'alert', id: 'pair-error' } })
  error.hidden = true
  const form = el(
    'form',
    { class: 'pair-form' },
    el('h1', { text: 'Podcast Reader' }),
    el('p', { class: 'muted', text: intro }),
    el('p', { class: 'muted', text: 'In the desktop app: Settings → Connect browser extension.' }),
    combinedInput,
    el('div', { class: 'pair-fields' }, portInput, codeInput),
    el('div', { class: 'actions' }, pairButton),
    error
  )
  form.addEventListener('submit', (event) => {
    event.preventDefault()
    const input = resolvePairingInput(combinedInput.value, portInput.value, codeInput.value)
    if (input === null) {
      error.textContent = 'Enter the combined string (port-code) or the port and 6-character code.'
      error.hidden = false
      return
    }
    pairButton.disabled = true
    error.hidden = true
    void (async () => {
      const result = await performPairing(input, {
        claim: (port, code) => claimToken(port, code),
        verify: (pairing) => new EngineClient(pairing).health()
      })
      if (!result.ok) {
        // Self-authored copy; a failure never touches a stored pairing.
        error.textContent =
          result.reason === 'unreachable'
            ? 'Could not reach the desktop app on that port — is it running?'
            : result.reason === 'rejected'
              ? 'The code was not accepted (wrong, expired, or already used). Mint a new one and retry.'
              : 'Pairing verification failed — mint a new code and retry.'
        error.hidden = false
        pairButton.disabled = false
        return
      }
      await store.setPairing(result.pairing)
      await init()
    })()
  })
  container.append(form)
}

function renderEngineDown(container: HTMLElement): void {
  const launchButton = el('button', { text: 'Open the desktop app', attrs: { id: 'launch-app' } })
  const retryButton = el('button', {
    text: 'Try again',
    class: 'secondary',
    attrs: { id: 'retry-probe' }
  })
  launchButton.addEventListener('click', () => {
    void (async () => {
      const url = await activeTabUrl()
      openProtocolLaunch(classifySource(url) === 'ineligible' ? undefined : url)
    })()
  })
  retryButton.addEventListener('click', () => void init())
  container.append(
    el('h1', { text: 'Podcast Reader' }),
    el('p', { class: 'muted', text: "The desktop app isn't running.", attrs: { id: 'engine-down' } }),
    el('div', { class: 'actions' }, launchButton, retryButton)
  )
}

/**
 * Launch the desktop app via its protocol registration. With an eligible
 * page URL the launch doubles as the confirm-gated protocol submission
 * (ext-jobs spec: that channel is unauthenticated, so the app holds it in
 * awaiting-confirmation); without one it still starts the app.
 */
function openProtocolLaunch(pageUrl: string | undefined): void {
  const target = `podcast-reader://transcribe?url=${encodeURIComponent(pageUrl ?? '')}`
  void chrome.tabs.create({ url: target })
}

// ---- connected: submit + progress ------------------------------------------------

async function renderConnected(container: HTMLElement, pairing: Pairing): Promise<void> {
  const client = new EngineClient(pairing)
  const tabUrl = await activeTabUrl()
  const kind: SourceKind = classifySource(tabUrl)

  const submitStatus = el('p', { class: 'muted', attrs: { role: 'status', id: 'submit-status' } })
  const jobList = el('div', { class: 'job-list', attrs: { id: 'job-list' } })
  container.append(el('h1', { text: 'Podcast Reader' }))

  if (kind === 'ineligible' || tabUrl === undefined) {
    container.append(el('p', { class: 'muted', text: "This page can't be transcribed." }))
  } else {
    const submitButton = el('button', { text: sourceLabel(kind), attrs: { id: 'submit-tab' } })
    const pageNote = el('p', { class: 'muted' })
    pageNote.textContent = tabUrl // page-derived string: textContent only (per U7)
    submitButton.addEventListener('click', () => {
      submitButton.disabled = true
      submitStatus.textContent = 'Submitting…'
      void (async () => {
        try {
          const record = await client.submitJob(tabUrl)
          await trackSubmission(record)
          submitStatus.textContent = 'Submitted.'
          views.set(record.id, viewFromRecord(record))
          renderJobs(jobList, client)
        } catch (err) {
          if (err instanceof EngineRequestError) {
            submitStatus.textContent = `The engine rejected the submission: ${err.detail}`
          } else {
            // Engine unreachable mid-session: offer the protocol fallback —
            // never silent extension-side queuing (ext-jobs spec).
            submitStatus.textContent = 'The desktop app stopped responding.'
            const fallback = el('button', {
              text: 'Open in the desktop app instead',
              class: 'secondary',
              attrs: { id: 'protocol-fallback' }
            })
            fallback.addEventListener('click', () => openProtocolLaunch(tabUrl))
            submitStatus.append(el('span', { text: ' ' }), fallback)
          }
        } finally {
          submitButton.disabled = false
        }
      })()
    })
    container.append(pageNote, el('div', { class: 'actions' }, submitButton), submitStatus)
  }

  container.append(jobList)
  await hydrateJobs(client)
  renderJobs(jobList, client)
  attachStream(client, jobList)
}

/** Hydrate every tracked job from its record — the source of truth (F3). */
async function hydrateJobs(client: EngineClient): Promise<void> {
  const tracked = await store.trackedJobs()
  views = new Map()
  let dirty = false
  for (const job of tracked) {
    try {
      const record = await client.getJob(job.id)
      views.set(job.id, viewFromRecord(record))
      // Seeing the popup counts as acknowledgment: badge math treats
      // terminal+notified as quiet, and the SW won't re-notify.
      if (isTerminal(record.state) && !job.notified) {
        job.notified = true
        dirty = true
      }
    } catch (err) {
      if (err instanceof EngineRequestError && err.status === 404) {
        dirty = true // job evicted engine-side; drop it from tracking
        continue
      }
    }
  }
  if (dirty) {
    await store.setTrackedJobs(tracked.filter((job) => views.has(job.id)))
  }
}

function attachStream(client: EngineClient, jobList: HTMLElement): void {
  streamAbort?.abort()
  const controller = new AbortController()
  streamAbort = controller
  void (async () => {
    try {
      const res = await client.openEvents(controller.signal)
      if (res.body === null) return
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      const parser = new SseParser()
      for (;;) {
        const { done, value } = await reader.read()
        if (done) return
        for (const payload of parser.push(decoder.decode(value, { stream: true }))) {
          let event: PipelineEvent
          try {
            event = JSON.parse(payload) as PipelineEvent
          } catch {
            continue
          }
          const result = applyEvent(views, event)
          views = result.views
          if (result.refreshJobId !== null) {
            try {
              const record = await client.getJob(result.refreshJobId)
              views.set(record.id, viewFromRecord(record))
            } catch {
              // hydration on next open covers the gap
            }
          }
          renderJobs(jobList, client)
        }
      }
    } catch {
      // The stream lives and dies with the popup; hydration covers gaps.
    }
  })()
}

function renderJobs(jobList: HTMLElement, client: EngineClient): void {
  jobList.replaceChildren()
  for (const view of views.values()) jobList.append(jobRow(view, client))
}

function jobRow(view: JobView, client: EngineClient): HTMLElement {
  const record = view.record
  const row = el(
    'div',
    { class: 'job-row', attrs: { 'data-job-id': record.id, 'data-state': record.state } },
    el('span', { class: 'job-title', text: record.title ?? record.source }),
    el('span', { class: 'job-state', text: record.state })
  )
  if (!isTerminal(record.state) && view.liveMessage !== null) {
    row.append(
      el('span', {
        class: 'job-step',
        text: view.liveStep === null ? view.liveMessage : `${view.liveStep}: ${view.liveMessage}`
      })
    )
  }
  if (record.error !== null) {
    row.append(
      el('span', { class: 'job-error', text: `${record.error.code}: ${record.error.message}` })
    )
    if (record.error.hint !== '') {
      row.append(el('span', { class: 'job-hint', text: record.error.hint }))
    }
    if (record.error.code === 'download_auth_required') {
      row.append(captureAffordance(record, client))
    }
  }
  return row
}

// ---- cookie capture (F2) -----------------------------------------------------------

function captureAffordance(record: JobRecord, client: EngineClient): HTMLElement {
  const target = captureTarget(record.source)
  if (target === null) {
    return el('span', { class: 'job-hint', text: 'This source has no domain to share a login for.' })
  }
  const button = el('button', {
    text: `Share your ${target.domain} login`,
    attrs: { id: `capture-${record.id}` }
  })
  const status = el('span', { class: 'muted', attrs: { role: 'status' } })
  const wrap = el('div', { class: 'actions' }, button, status)
  button.addEventListener('click', () => {
    button.disabled = true
    void (async () => {
      // Permission requested at click time, for this registrable domain
      // only (ext-cookie-capture spec); declining changes nothing.
      const granted = await chrome.permissions.request({
        permissions: ['cookies'],
        origins: [...target.origins]
      })
      if (!granted) {
        status.textContent = 'Permission declined — nothing was read or shared.'
        button.disabled = false
        return
      }
      try {
        // URL-keyed query so parent-domain cookies are included, unioned
        // with the domain-keyed query for sibling-subdomain cookies the
        // URL key would miss (per U4).
        const [byUrl, byDomain] = await Promise.all([
          chrome.cookies.getAll({ url: target.queryUrl }),
          chrome.cookies.getAll({ domain: target.domain })
        ])
        const cookies = uniqueCookies(byUrl, byDomain)
        if (cookies.length === 0) {
          status.textContent = `No ${target.domain} cookies found — log in there first.`
          button.disabled = false
          return
        }
        // Cookie values live only in this transaction: serialized, pushed,
        // and dropped — never stored, never logged.
        await client.putCookies(target.domain, serializeNetscape(cookies))
        status.textContent = 'Login shared.'
        const retry = el('button', {
          text: 'Retry the transcription',
          class: 'secondary',
          attrs: { id: `retry-${record.id}` }
        })
        retry.addEventListener('click', () => {
          retry.disabled = true
          void (async () => {
            try {
              const resubmitted = await client.submitJob(record.source)
              await trackSubmission(resubmitted)
              views.set(resubmitted.id, viewFromRecord(resubmitted))
              const jobList = document.getElementById('job-list')
              if (jobList !== null) renderJobs(jobList, client)
            } catch {
              status.textContent = 'Resubmission failed — try again from the popup.'
              retry.disabled = false
            }
          })()
        })
        wrap.append(retry)
      } catch (err) {
        // Self-authored failure copy: the engine detail references line
        // numbers only (engine/cookies.py), never cookie content.
        status.textContent =
          err instanceof EngineRequestError
            ? `The engine rejected the login data: ${err.detail}`
            : 'Sharing failed — the desktop app may have stopped.'
        button.disabled = false
      }
    })()
  })
  return wrap
}

// ---- helpers -----------------------------------------------------------------------

/** The active tab's URL, readable under the click-granted `activeTab` (per U1). */
async function activeTabUrl(): Promise<string | undefined> {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true })
  return tab?.url
}
