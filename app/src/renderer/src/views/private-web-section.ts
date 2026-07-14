import { el } from '../dom'
import { extractEngineDetail } from '../engine-error'
import type { PrivateWebStatus } from '../../../shared/ipc'

export interface PrivateWebSection {
  cleanup(): void
}

export function privateWebLabel(status: PrivateWebStatus): string {
  switch (status.state) {
    case 'disabled':
      return 'Off. Nothing is exposed to your tailnet.'
    case 'starting':
      return 'Checking Tailscale Serve…'
    case 'ready':
      return `Ready at ${status.url}`
    case 'conflict':
      return `Not changed: ${status.message}`
    case 'error':
      return `Unavailable: ${status.message}`
  }
}

export function privateWebActions(status: PrivateWebStatus): readonly string[] {
  if (status.state === 'ready') return ['disable']
  if (status.state === 'conflict' || status.state === 'error') return ['retry', 'disable']
  if (status.state === 'starting') return []
  return ['enable']
}

export function mountPrivateWebSection(container: HTMLElement): PrivateWebSection {
  const statusText = el('p', { class: 'section-note', attrs: { role: 'status' } })
  const toggle = el('button', { attrs: { type: 'button' } })
  const disable = el('button', {
    text: 'Disable private web access',
    attrs: { type: 'button' }
  })
  let current: PrivateWebStatus = { state: 'disabled' }
  let disposed = false

  function render(status: PrivateWebStatus): void {
    current = status
    statusText.textContent = privateWebLabel(status)
    statusText.classList.toggle('error-text', status.state === 'error' || status.state === 'conflict')
    toggle.textContent =
      status.state === 'ready'
        ? 'Disable private web access'
        : status.state === 'conflict' || status.state === 'error'
          ? 'Try private web access again'
          : 'Enable private web access'
    toggle.disabled = status.state === 'starting'
    disable.hidden = status.state !== 'conflict' && status.state !== 'error'
  }

  toggle.addEventListener('click', () => {
    toggle.disabled = true
    window.api
      .setPrivateWebEnabled(current.state !== 'ready')
      .then((status) => {
        if (!disposed) render(status)
      })
      .catch((cause: unknown) => {
        if (!disposed) render({ state: 'error', message: extractEngineDetail(cause) })
      })
  })

  disable.addEventListener('click', () => {
    disable.disabled = true
    window.api
      .setPrivateWebEnabled(false)
      .then((status) => {
        if (!disposed) render(status)
      })
      .catch((cause: unknown) => {
        if (!disposed) render({ state: 'error', message: extractEngineDetail(cause) })
      })
      .finally(() => {
        disable.disabled = false
      })
  })

  container.append(
    el('h3', { text: 'Private web access' }),
    el('p', {
      class: 'section-note',
      text:
        'Read your library from another device on your Tailscale network. ' +
        'Podcast Reader uses Tailscale Serve only—never public Funnel access.'
    }),
    toggle,
    disable,
    statusText
  )

  void window.api.getPrivateWebStatus().then((status) => {
    if (!disposed) render(status)
  })
  const unsubscribe = window.api.onPrivateWebStatus((status) => {
    if (!disposed) render(status)
  })
  render(current)

  return {
    cleanup: () => {
      disposed = true
      unsubscribe()
    }
  }
}
