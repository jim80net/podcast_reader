import { el } from '../dom'
import { extractEngineDetail } from '../engine-error'
import { LatestGate } from '../latest-gate'
import { packErrorText, packProgressBar, packStateBadge } from '../pack-ui'
import {
  applyPackEvent,
  defaultSelection,
  deriveWhisperDevice,
  formatBytes,
  hardwareSummary,
  installableNow,
  selectionInstalled,
  setupNeeded
} from '../packs-store'
import { navigate } from '../router'
import type { ViewCleanup } from '../store'
import type { HardwareInfo, PackStatus } from '../../../shared/types'

/**
 * First-run setup wizard (app-setup-ui spec, task 6.2): hardware summary and
 * the pack list from `GET /v1/packs`, recommended packs pre-checked with
 * sizes, install with live progress from forwarded pack events, resume for
 * `resumable` packs (the same install request — the engine resumes its
 * identity-bound partials), and a skip. Installing sets `whisper_device`
 * from detected hardware via PUT /v1/settings (per S4). Completing or
 * skipping marks the app-side first-run flag; the view itself is stateless
 * across navigation — pack state lives in the engine, so leaving and coming
 * back re-hydrates losslessly.
 */
export function mountSetup(container: HTMLElement): ViewCleanup {
  const status = el('p', { class: 'view-status', text: 'Checking your hardware…' })
  const hardwareLine = el('p', { class: 'hardware-summary' })
  const deviceLine = el('p', { class: 'device-note' })
  const list = el('div', { class: 'pack-list', attrs: { role: 'list' } })
  const actionError = el('p', { class: 'error-text', attrs: { role: 'alert' } })
  actionError.hidden = true
  const installButton = el('button', {
    text: 'Install selected',
    attrs: { type: 'button', id: 'setup-install' }
  })
  const finishButton = el('button', {
    text: 'Finish setup',
    attrs: { type: 'button', id: 'setup-finish' }
  })
  const skipButton = el('button', {
    text: 'Skip for now',
    class: 'button-secondary',
    attrs: { type: 'button', id: 'setup-skip' }
  })
  container.append(
    el('h2', { text: 'Set up Podcast Reader' }),
    el('p', {
      class: 'setup-intro',
      text:
        'Podcast Reader downloads its transcription components on first run. ' +
        'Everything here can be changed later under Settings → Packs.'
    }),
    status,
    hardwareLine,
    deviceLine,
    list,
    el('div', { class: 'form-actions setup-actions' }, installButton, finishButton, skipButton),
    actionError
  )

  let disposed = false
  let loaded = false
  let hardware: HardwareInfo | null = null
  let packs: readonly PackStatus[] = []
  const selection = new Set<string>()
  let selectionInitialized = false
  const gate = new LatestGate()

  async function load(): Promise<void> {
    const isLatest = gate.next()
    try {
      const response = await window.api.listPacks()
      if (disposed || !isLatest()) return
      hardware = response.hardware
      packs = response.packs
      if (!selectionInitialized) {
        selectionInitialized = true
        for (const id of defaultSelection(packs)) selection.add(id)
      }
      loaded = true
      render()
    } catch (err) {
      if (disposed || !isLatest() || loaded) return
      status.textContent = `Pack information unavailable: ${extractEngineDetail(err)}`
      status.classList.add('error-text')
    }
  }

  function render(): void {
    if (hardware === null) return
    status.textContent = ''
    status.classList.remove('error-text')
    hardwareLine.textContent = hardwareSummary(hardware)
    const device = deriveWhisperDevice(hardware, packs)
    deviceLine.textContent =
      device === 'cuda'
        ? 'Transcription will use your NVIDIA GPU (device: cuda).'
        : 'Transcription will run on the CPU (device: cpu).'
    renderList()
    const installing = packs.some((pack) => pack.state === 'installing')
    installButton.disabled =
      installing ||
      ![...selection].some((id) => {
        const pack = packs.find((entry) => entry.id === id)
        return pack !== undefined && installableNow(pack.state)
      })
    finishButton.hidden = !(selectionInstalled(packs, selection) || !setupNeeded(packs))
  }

  function renderList(): void {
    list.replaceChildren()
    for (const pack of packs) {
      if (pack.state === 'unavailable') continue // nothing a first run could install (per S5)
      list.append(packRow(pack))
    }
  }

  function packRow(pack: PackStatus): HTMLElement {
    const checkbox = el('input', {
      attrs: { type: 'checkbox', id: `setup-pack-${pack.id}` }
    })
    checkbox.checked = pack.state === 'installed' || selection.has(pack.id)
    checkbox.disabled = pack.state === 'installed' || pack.state === 'installing'
    checkbox.addEventListener('change', () => {
      if (checkbox.checked) selection.add(pack.id)
      else selection.delete(pack.id)
      render()
    })
    const name = pack.recommended ? `${pack.display_name} (recommended)` : pack.display_name
    const body = el(
      'div',
      { class: 'pack-row-body' },
      el(
        'div',
        { class: 'pack-row-head' },
        el('label', { class: 'pack-name', text: name, attrs: { for: checkbox.id } }),
        el('span', { class: 'pack-size', text: formatBytes(pack.size) }),
        packStateBadge(pack)
      )
    )
    const row = el(
      'div',
      {
        class: 'pack-row',
        attrs: { role: 'listitem', 'data-pack-id': pack.id, 'data-state': pack.state }
      },
      checkbox,
      body
    )
    if (pack.state === 'resumable') {
      body.append(
        el('p', {
          class: 'pack-note',
          text: 'Partially downloaded — installing resumes where it left off.'
        })
      )
    }
    const progress = packProgressBar(pack)
    if (progress !== null) body.append(progress)
    const error = packErrorText(pack)
    if (error !== null) body.append(error)
    return row
  }

  async function installSelected(): Promise<void> {
    if (hardware === null) return
    actionError.hidden = true
    installButton.disabled = true
    try {
      // Device defaulting first (per S4): the wizard is the moment
      // whisper_device starts reflecting real hardware, never a stale default.
      const device = deriveWhisperDevice(hardware, packs)
      const settings = await window.api.getSettings()
      await window.api.putSettings({ ...settings, whisper_device: device })
      for (const id of selection) {
        const pack = packs.find((entry) => entry.id === id)
        if (pack !== undefined && installableNow(pack.state)) await window.api.installPack(id)
      }
      if (disposed) return
      await load()
    } catch (err) {
      if (disposed) return
      actionError.textContent = extractEngineDetail(err)
      actionError.hidden = false
      render() // restore button state from pack reality
    }
  }

  function complete(): void {
    finishButton.disabled = skipButton.disabled = true
    window.api
      .markFirstRunComplete()
      .then(() => {
        if (!disposed) navigate({ view: 'library' })
      })
      .catch((err: unknown) => {
        if (disposed) return
        actionError.textContent = extractEngineDetail(err)
        actionError.hidden = false
        finishButton.disabled = skipButton.disabled = false
      })
  }

  installButton.addEventListener('click', () => void installSelected())
  finishButton.addEventListener('click', complete)
  skipButton.addEventListener('click', complete)

  void load()
  const unsubscribers = [
    window.api.onPipelineEvent((event) => {
      const result = applyPackEvent(packs, event)
      if (!result.isPackEvent) return
      packs = result.packs
      if (loaded) render()
      if (result.needsRefresh) void load()
    }),
    // After every SSE (re)connect the jobs hydration push fires — re-hydrate
    // pack state too, since pack events may have been missed while dropped.
    window.api.onJobsHydrated(() => {
      if (loaded) void load()
    }),
    window.api.onEngineStatus((engineStatus) => {
      if (engineStatus.state === 'ready' && !loaded) void load()
    })
  ]

  return () => {
    disposed = true
    for (const unsubscribe of unsubscribers) unsubscribe()
  }
}
