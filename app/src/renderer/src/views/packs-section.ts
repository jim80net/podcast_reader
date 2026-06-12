import { el } from '../dom'
import { extractEngineDetail } from '../engine-error'
import { LatestGate } from '../latest-gate'
import { packErrorText, packProgressBar, packStateBadge } from '../pack-ui'
import { applyPackEvent, cudaAdvisoryNeeded, formatBytes } from '../packs-store'
import { navigate } from '../router'
import type { PackStatus } from '../../../shared/types'

/**
 * Settings → Packs section (app-setup-ui spec, task 6.3): every registry
 * pack with state/version/size/progress (`unavailable` for unpublished
 * entries, per S5), install/uninstall with engine 409 reasons surfaced
 * inline (409 now only while installing, per S1), an explicit re-download
 * affordance for `incompatible`/`failed` packs (per S8), structured errors,
 * a "Run setup again" entry to the wizard, and the cuda-without-pack
 * advisory (per S4/Q2) — uninstall never mutates `whisper_device`.
 *
 * License attributions from installed manifests are NOT rendered yet: the
 * engine's PackStatus payload does not carry them (see tasks.md 6.3 note);
 * wiring lands with task 8.1 once the engine exposes the notices.
 */

export interface PacksSection {
  /** Re-evaluate the cuda advisory after the device select changes. */
  deviceChanged(): void
  cleanup(): void
}

export function mountPacksSection(container: HTMLElement, getDevice: () => string): PacksSection {
  const rerunButton = el('button', {
    text: 'Run setup again',
    class: 'button-secondary',
    attrs: { type: 'button', id: 'settings-run-setup' }
  })
  rerunButton.addEventListener('click', () => navigate({ view: 'setup' }))
  const advisory = el('p', {
    class: 'banner warning-banner cuda-advisory',
    text:
      'The transcription device is set to cuda, but no usable CUDA runtime ' +
      'pack is installed — jobs will run on the CPU until it is installed.',
    attrs: { role: 'alert' }
  })
  advisory.hidden = true
  const status = el('p', { class: 'view-status', text: 'Loading packs…' })
  const list = el('div', { class: 'pack-list', attrs: { role: 'list' } })
  container.append(
    el('div', { class: 'packs-header' }, el('h3', { text: 'Packs' }), rerunButton),
    advisory,
    status,
    list
  )

  let disposed = false
  let loaded = false
  let packs: readonly PackStatus[] = []
  const gate = new LatestGate()
  /** Row-level failure details (e.g. an uninstall 409 reason), kept across re-renders. */
  const actionErrors = new Map<string, string>()

  async function load(): Promise<void> {
    const isLatest = gate.next()
    try {
      const response = await window.api.listPacks()
      if (disposed || !isLatest()) return
      packs = response.packs
      loaded = true
      render()
    } catch (err) {
      if (disposed || !isLatest() || loaded) return
      status.textContent = `Packs unavailable: ${extractEngineDetail(err)}`
      status.classList.add('error-text')
    }
  }

  function render(): void {
    status.textContent = ''
    status.classList.remove('error-text')
    updateAdvisory()
    list.replaceChildren()
    for (const pack of packs) list.append(packRow(pack))
  }

  function updateAdvisory(): void {
    advisory.hidden = !cudaAdvisoryNeeded(getDevice(), packs)
  }

  function packRow(pack: PackStatus): HTMLElement {
    const head = el(
      'div',
      { class: 'pack-row-head' },
      el('span', { class: 'pack-name', text: pack.display_name }),
      el('span', {
        class: 'pack-size',
        text:
          pack.installed_version !== null
            ? `v${pack.installed_version} · ${formatBytes(pack.size)}`
            : formatBytes(pack.size)
      }),
      packStateBadge(pack)
    )
    const body = el('div', { class: 'pack-row-body' }, head)
    if (pack.state === 'unavailable') {
      body.append(el('p', { class: 'pack-note', text: 'Not yet available for download.' }))
    }
    const progress = packProgressBar(pack)
    if (progress !== null) body.append(progress)
    const structured = packErrorText(pack)
    if (structured !== null) body.append(structured)
    const actions = packActions(pack)
    if (actions !== null) body.append(actions)
    const actionError = actionErrors.get(pack.id)
    if (actionError !== undefined) {
      body.append(
        el('p', { class: 'error-text pack-error', text: actionError, attrs: { role: 'alert' } })
      )
    }
    return el(
      'div',
      {
        class: 'pack-row',
        attrs: { role: 'listitem', 'data-pack-id': pack.id, 'data-state': pack.state }
      },
      body
    )
  }

  function packActions(pack: PackStatus): HTMLElement | null {
    const buttons: HTMLButtonElement[] = []
    if (pack.state === 'not-installed') buttons.push(actionButton(pack, 'Install', install))
    if (pack.state === 'resumable') buttons.push(actionButton(pack, 'Resume download', install))
    if (pack.state === 'failed' || pack.state === 'incompatible') {
      // The re-download affordance (per S8): one action fetches the
      // compatible/intact version — the engine re-requests the install.
      buttons.push(actionButton(pack, 'Re-download', install))
    }
    if (
      pack.state === 'installed' ||
      pack.state === 'incompatible' ||
      pack.state === 'failed'
    ) {
      buttons.push(actionButton(pack, 'Uninstall', uninstall, 'button-secondary'))
    }
    if (buttons.length === 0) return null
    return el('div', { class: 'pack-actions' }, ...buttons)
  }

  function actionButton(
    pack: PackStatus,
    label: string,
    run: (packId: string) => Promise<void>,
    className?: string
  ): HTMLButtonElement {
    const props = className === undefined ? { text: label } : { text: label, class: className }
    const button = el('button', { ...props, attrs: { type: 'button' } })
    button.addEventListener('click', () => {
      button.disabled = true
      actionErrors.delete(pack.id)
      run(pack.id)
        .then(() => {
          if (!disposed) void load()
        })
        .catch((err: unknown) => {
          if (disposed) return
          // Engine refusals (e.g. 409 while installing, per S1) land inline
          // on the row with the engine's self-authored reason.
          actionErrors.set(pack.id, extractEngineDetail(err))
          void load()
        })
    })
    return button
  }

  const install = (packId: string): Promise<void> => window.api.installPack(packId)
  const uninstall = (packId: string): Promise<void> => window.api.uninstallPack(packId)

  void load()
  const unsubscribers = [
    window.api.onPipelineEvent((event) => {
      const result = applyPackEvent(packs, event)
      if (!result.isPackEvent) return
      packs = result.packs
      if (loaded) render()
      if (result.needsRefresh) void load()
    }),
    // SSE (re)connect → jobs hydration push → pack events may have been
    // missed while the stream was down; re-hydrate pack state too.
    window.api.onJobsHydrated(() => {
      if (loaded) void load()
    })
  ]

  return {
    deviceChanged: updateAdvisory,
    cleanup: () => {
      disposed = true
      for (const unsubscribe of unsubscribers) unsubscribe()
    }
  }
}
