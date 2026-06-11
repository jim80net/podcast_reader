import { el } from '../dom'
import { extractEngineDetail } from '../engine-error'
import { hrefFor } from '../router'
import type { ViewCleanup } from '../store'

/**
 * Reader view (app-views spec + design decision 8): the artifact HTML is
 * fetched main-side with auth and injected verbatim via `iframe.srcdoc` with
 * `sandbox="allow-scripts"` ONLY — no `allow-same-origin`, so the artifact
 * (and its inline chapter scroll-sync script) runs in an opaque origin with
 * no parent access, no IPC bridge, and no engine token.
 */
export function mountReader(container: HTMLElement, sourceId: string): ViewCleanup {
  const status = el('p', { class: 'view-status', text: 'Loading transcript…' })
  const frame = el('iframe', {
    class: 'reader-frame',
    attrs: { sandbox: 'allow-scripts', title: 'Transcript' }
  })
  frame.hidden = true
  container.append(
    el(
      'p',
      { class: 'reader-back' },
      el('a', { text: '← Library', attrs: { href: hrefFor({ view: 'library' }) } })
    ),
    status,
    frame
  )

  let disposed = false
  void window.api
    .transcriptHtml(sourceId)
    .then((html) => {
      if (disposed) return
      frame.srcdoc = html
      frame.hidden = false
      status.remove()
    })
    .catch((err: unknown) => {
      if (disposed) return
      status.textContent = `Transcript unavailable: ${extractEngineDetail(err)}`
      status.classList.add('error-text')
    })

  return () => {
    disposed = true
  }
}
