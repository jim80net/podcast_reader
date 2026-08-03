import { el } from './dom'
import type { PremiumAdSlot } from '../../shared/ipc'
import type { ViewCleanup } from './store'

/** Native, text-only house-ad mount. Empty and rejected inventory leaves no layout. */
export function mountPremiumAdSlot(container: HTMLElement, slot: PremiumAdSlot): ViewCleanup {
  let disposed = false
  let generation = 0
  let expiryTimer: ReturnType<typeof setTimeout> | null = null

  const clear = (): void => {
    generation += 1
    if (expiryTimer !== null) clearTimeout(expiryTimer)
    expiryTimer = null
    container.replaceChildren()
  }

  const load = async (): Promise<void> => {
    const requestGeneration = ++generation
    try {
      const inventory = await window.api.getPremiumInventory(slot)
      if (disposed || requestGeneration !== generation || inventory === null || inventory.expiresAt <= Date.now()) return
      const cta = el('button', {
        class: 'house-ad-cta',
        text: 'Learn more',
        attrs: { type: 'button' }
      })
      cta.addEventListener('click', () => {
        void window.api.openPremiumCta(slot, inventory.creative.ctaUrl).catch(clear)
      })
      container.replaceChildren(
        el(
          'aside',
          { class: 'house-ad-card', attrs: { 'aria-label': 'Sponsored recommendation' } },
          el('span', { class: 'house-ad-label', text: 'Sponsored' }),
          el('h3', { class: 'house-ad-title', text: inventory.creative.title }),
          el('p', { class: 'house-ad-body', text: inventory.creative.body }),
          cta
        )
      )
      expiryTimer = setTimeout(clear, Math.max(0, inventory.expiresAt - Date.now()))
    } catch {
      if (!disposed && requestGeneration === generation) clear()
    }
  }

  const unsubscribeInvalidated = window.api.onPremiumInvalidated(clear)
  const unsubscribeState = window.api.onPremiumState((state) => {
    clear()
    if (state.state === 'online-free') void load()
  })
  const onFocus = (): void => { if (container.childElementCount === 0) void load() }
  window.addEventListener('focus', onFocus)
  void load()

  return () => {
    disposed = true
    clear()
    unsubscribeInvalidated()
    unsubscribeState()
    window.removeEventListener('focus', onFocus)
  }
}
