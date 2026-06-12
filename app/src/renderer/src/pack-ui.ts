import { el } from './dom'
import { formatBytes, progressPercent } from './packs-store'
import type { PackStatus } from '../../shared/types'

/**
 * Small shared DOM pieces for pack rows — used by both the setup wizard and
 * the Settings Packs section so state badges, progress bars, and structured
 * errors look and behave identically. Pure construction via dom.ts only.
 */

/** State badge, styled by data-state like the job badges. */
export function packStateBadge(pack: PackStatus): HTMLElement {
  return el('span', {
    class: 'pack-state',
    text: pack.state,
    attrs: { 'data-state': pack.state }
  })
}

/** Download progress bar + byte counter for an installing pack (else null). */
export function packProgressBar(pack: PackStatus): HTMLElement | null {
  if (pack.state !== 'installing') return null
  const percent = progressPercent(pack.progress)
  const fill = el('div', { class: 'progress-fill' })
  fill.style.width = `${percent}%`
  const bar = el(
    'div',
    {
      class: 'progress',
      attrs: {
        role: 'progressbar',
        'aria-valuemin': '0',
        'aria-valuemax': '100',
        'aria-valuenow': String(percent)
      }
    },
    fill
  )
  const counter =
    pack.progress === null
      ? 'starting download…'
      : `${formatBytes(pack.progress.bytes)} of ${formatBytes(pack.progress.total)}`
  return el('div', { class: 'pack-progress' }, bar, el('span', { class: 'pack-progress-text', text: counter }))
}

/** Structured pack error ({code, message}) for failed/incompatible packs. */
export function packErrorText(pack: PackStatus): HTMLElement | null {
  if (pack.error === null) return null
  return el('p', {
    class: 'error-text pack-error',
    text: `${pack.error.code}: ${pack.error.message}`,
    attrs: { role: 'alert' }
  })
}
