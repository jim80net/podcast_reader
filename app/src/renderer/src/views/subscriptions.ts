import { el } from '../dom'
import { hrefFor } from '../router'
import type { PremiumProductState, SubscriptionSummary } from '../../../shared/ipc'
import type { ViewCleanup } from '../store'

export function mountSubscriptions(container: HTMLElement): ViewCleanup {
  let disposed = false
  let state: PremiumProductState = { state: 'local', available: true }
  let subscriptions: SubscriptionSummary[] = []

  const status = el('p', { class: 'section-note', attrs: { role: 'status', 'aria-live': 'polite' } })
  const feedUrl = el('input', {
    attrs: { type: 'url', name: 'feed-url', maxlength: '2048', autocomplete: 'off', placeholder: 'https://example.com/feed.xml', 'aria-label': 'Podcast feed URL' }
  })
  const add = el('button', { text: 'Add podcast', attrs: { type: 'submit' } })
  const form = el('form', { class: 'subscription-form' }, feedUrl, add)
  const account = el('button', { text: 'Connect account', attrs: { type: 'button' } })
  const list = el('div', { class: 'subscription-list' })
  container.append(
    el('h1', { text: 'Subscriptions' }),
    el('p', { text: 'New episodes are checked and transcribed on this computer. Feed details never go to the premium service.' }),
    status,
    account,
    form,
    list
  )

  const enabled = (): boolean => subscriptionControls(state).mutations

  const renderStatus = (): void => {
    account.hidden = true
    feedUrl.disabled = !enabled()
    add.disabled = !enabled()
    status.textContent = subscriptionStatus(state)
    if (state.available && state.state === 'local') {
      account.hidden = false
    }
  }

  const renderList = (): void => {
    list.replaceChildren()
    if (subscriptions.length === 0) {
      list.append(el('p', { class: 'empty', text: 'No podcast subscriptions yet.' }))
      return
    }
    for (const subscription of subscriptions) {
      const poll = el('button', { text: 'Poll now', attrs: { type: 'button' } })
      const remove = el('button', { text: 'Remove', attrs: { type: 'button' } })
      poll.disabled = !enabled()
      remove.disabled = !enabled()
      poll.addEventListener('click', () => {
        if (!enabled()) return
        poll.disabled = true
        void window.api.pollSubscription(subscription.id).then((result) => {
          subscriptions = subscriptions.map((item) => item.id === result.subscription.id ? result.subscription : item)
          if (!disposed) { status.textContent = result.discoveredCount === 0 ? 'No new episodes found.' : `${result.discoveredCount} new episode${result.discoveredCount === 1 ? '' : 's'} queued.`; renderList() }
        }).catch(() => { if (!disposed) { status.textContent = 'The subscription could not be polled.'; renderList() } })
      })
      remove.addEventListener('click', () => {
        if (!enabled()) return
        remove.disabled = true
        void window.api.deleteSubscription(subscription.id).then(() => {
          subscriptions = subscriptions.filter((item) => item.id !== subscription.id)
          if (!disposed) renderList()
        }).catch(() => { if (!disposed) { status.textContent = 'The subscription could not be removed.'; renderList() } })
      })
      const details = [
        subscription.origin,
        subscription.lastCheckedAt === null ? 'Not checked yet' : `Last checked ${formatTime(subscription.lastCheckedAt)}`,
        subscription.nextCheckAt === null ? 'Next check paused' : `Next check ${formatTime(subscription.nextCheckAt)}`
      ]
      if (subscription.lastErrorCode !== null) details.push('Last check reported a local feed error')
      list.append(el(
        'section',
        { class: 'card subscription-card' },
        el('h2', { text: subscription.title ?? subscription.origin }),
        el('p', { class: 'section-note', text: details.join(' · ') }),
        el('div', { class: 'button-row' }, poll, remove, el('a', { text: 'Open Library', attrs: { href: hrefFor({ view: 'library' }) } }))
      ))
    }
  }

  account.addEventListener('click', () => {
    account.disabled = true
    void window.api.connectPremiumAccount().then((next) => { if (!disposed) { state = next; renderStatus(); renderList() } })
      .catch(() => { if (!disposed) { status.textContent = 'Account connection did not complete.'; account.disabled = false } })
  })
  form.addEventListener('submit', (event) => {
    event.preventDefault()
    if (!enabled()) return
    add.disabled = true
    void window.api.createSubscription(feedUrl.value).then((created) => {
      subscriptions = [...subscriptions, created]
      feedUrl.value = ''
      if (!disposed) { renderStatus(); renderList() }
    }).catch(() => { if (!disposed) { status.textContent = 'The feed could not be added.'; add.disabled = false } })
  })

  const unsubscribeState = window.api.onPremiumState((next) => {
    state = next
    if (!disposed) { renderStatus(); renderList() }
  })
  void Promise.all([window.api.getPremiumState(), window.api.listSubscriptions()]).then(([next, local]) => {
    if (!disposed) { state = next; subscriptions = local; renderStatus(); renderList() }
  }).catch(() => { if (!disposed) { status.textContent = 'The local subscription list is unavailable.'; feedUrl.disabled = true; add.disabled = true } })
  renderStatus()
  renderList()
  return () => { disposed = true; unsubscribeState() }
}

export function subscriptionControls(state: PremiumProductState): { mutations: boolean; connect: boolean } {
  return {
    mutations: state.state === 'online-premium' && state.subscriptionsAvailable,
    connect: state.available && state.state === 'local'
  }
}

export function subscriptionStatus(state: PremiumProductState): string {
  if (!state.available) return 'Account service is not configured for this build. Local subscriptions remain visible.'
  if (state.state === 'local') return 'Connect a premium account to add or poll subscriptions.'
  if (state.state === 'online-free') return 'Podcast subscriptions require premium. The entered feed URL will not be sent.'
  if (state.state === 'online-unavailable') return 'Online features are unavailable. Subscription polling is paused; your local list is retained.'
  if (!state.subscriptionsAvailable) return 'Podcast subscriptions are not enabled for this premium account.'
  return 'Premium subscription polling is active while the app and account status are current.'
}

function formatTime(value: string): string {
  const parsed = Date.parse(value)
  return Number.isFinite(parsed) ? new Date(parsed).toLocaleString() : 'unknown'
}
