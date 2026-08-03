import { el } from '../dom'
import type { PremiumProductState } from '../../../shared/ipc'
import type { ViewCleanup } from '../store'

export function mountPremiumAccountSection(container: HTMLElement): ViewCleanup {
  let disposed = false
  const status = el('p', { class: 'section-note', attrs: { role: 'status', 'aria-live': 'polite' } })
  const action = el('button', { attrs: { type: 'button' } })
  container.append(el('h3', { text: 'Premium account' }), status, action)

  const render = (state: PremiumProductState): void => {
    action.hidden = false
    action.disabled = false
    if (!state.available) {
      status.textContent = 'Account service is not configured for this build.'
      action.hidden = true
    } else if (state.state === 'local') {
      status.textContent = 'Not connected. Local use stays private and ad-free.'
      action.textContent = 'Connect account'
      action.dataset['action'] = 'connect'
    } else if (state.state === 'online-free') {
      status.textContent = 'Connected on the free tier. Plain-text house recommendations may appear.'
      action.textContent = 'Sign out'
      action.dataset['action'] = 'sign-out'
    } else if (state.state === 'online-premium') {
      status.textContent = 'Premium is active. Recommendations are off.'
      action.textContent = 'Sign out'
      action.dataset['action'] = 'sign-out'
    } else {
      status.textContent = 'Account features are temporarily unavailable. Local features still work.'
      action.textContent = 'Reconnect'
      action.dataset['action'] = 'connect'
    }
  }

  const refresh = async (): Promise<void> => {
    try {
      const state = await window.api.getPremiumState()
      if (!disposed) render(state)
    } catch {
      if (!disposed) {
        status.textContent = 'Account status is unavailable. Local features still work.'
        action.hidden = true
      }
    }
  }

  action.addEventListener('click', () => {
    action.disabled = true
    status.textContent = action.dataset['action'] === 'sign-out'
      ? 'Signing out…'
      : 'Continue in your browser to connect…'
    const request = action.dataset['action'] === 'sign-out'
      ? window.api.signOutPremiumAccount()
      : window.api.connectPremiumAccount()
    void request.then((state) => { if (!disposed) render(state) }).catch(() => {
      if (!disposed) {
        status.textContent = 'Account connection did not complete. Local features are unchanged.'
        action.disabled = false
      }
    })
  })

  const unsubscribeInvalidated = window.api.onPremiumInvalidated(() => {
    status.textContent = 'Refreshing account status…'
    action.disabled = true
  })
  const unsubscribeState = window.api.onPremiumState(render)
  void refresh()
  return () => { disposed = true; unsubscribeInvalidated(); unsubscribeState() }
}
