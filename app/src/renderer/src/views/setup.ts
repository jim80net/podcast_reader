import { customUrlVisible, planChapterSave, providerDocsUrl } from '../chapter-onboarding'
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
import { keyPlaceholder } from '../settings-form'
import type { ViewCleanup } from '../store'
import type { HardwareInfo, PackStatus, ProviderInfo } from '../../../shared/types'

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
  // Presentation-only sectioning (native-app-first-impression): a welcoming
  // hero, then a clearly-labelled hardware section and a components section.
  // The flow — hardware summary, recommended-pack selection, install with
  // progress, the first-run gate — is unchanged from below.
  const hero = el(
    'div',
    { class: 'setup-hero' },
    el('div', { class: 'setup-mark', text: '▶', attrs: { 'aria-hidden': 'true' } }),
    el('h2', { class: 'setup-title', text: 'Welcome to Podcast Reader' }),
    el('p', {
      class: 'setup-intro',
      text:
        'Everything runs on your computer — your audio never leaves it. First, ' +
        "let's download the speech model that fits your hardware. (Want chapter " +
        'markers and clean, idea-based paragraphs? Add an AI model in Settings — optional.)'
    })
  )
  const hardwareSection = el(
    'section',
    { class: 'setup-section' },
    el('h3', { class: 'setup-section-title', text: 'Your hardware' }),
    status,
    hardwareLine,
    deviceLine
  )
  const componentsSection = el(
    'section',
    { class: 'setup-section' },
    el('h3', { class: 'setup-section-title', text: 'Components to install' }),
    el('p', {
      class: 'setup-section-note',
      text: 'Recommended components are pre-selected. Sizes are shown so you can choose.'
    }),
    list
  )
  const chapter = buildChapterSection(() => disposed)
  container.append(
    hero,
    hardwareSection,
    componentsSection,
    chapter.section,
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
    // Install and Finish are mutually exclusive by state: an install in
    // progress shows an "Installing…" affordance with Finish hidden; once
    // everything selected is installed (or setup isn't needed at all) Install
    // disappears and Finish takes its place. "Skip for now" stays available
    // until either Finish or Skip completes the wizard.
    const installing = packs.some((pack) => pack.state === 'installing')
    const done = selectionInstalled(packs, selection) || !setupNeeded(packs)
    installButton.textContent = installing ? 'Installing…' : 'Install selected'
    installButton.disabled =
      installing ||
      ![...selection].some((id) => {
        const pack = packs.find((entry) => entry.id === id)
        return pack !== undefined && installableNow(pack.state)
      })
    // Hide Install once there is nothing left to install; never show Finish
    // while an install is still running.
    installButton.hidden = done && !installing
    finishButton.hidden = installing || !done
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
  // The chapter-provider section loads its own providers list independently of
  // pack state — a failure degrades the section to "set this up later" and
  // never blocks Finish/Skip (the no-block guarantee).
  void chapter.load()
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
      if (engineStatus.state === 'ready' && !loaded) {
        void load()
        void chapter.load()
      }
    })
  ]

  return () => {
    disposed = true
    for (const unsubscribe of unsubscribers) unsubscribe()
  }
}

interface ChapterSection {
  section: HTMLElement
  load(): Promise<void>
}

/**
 * Optional "AI model" onboarding section (wizard-chapter-provider design):
 * value-prop copy + benefits, a provider dropdown from `GET /v1/providers`
 * (built-ins + `custom`), a custom base-URL field shown only for `custom`, a
 * masked write-only key field with engine-side Test, and a Save that pushes
 * the key (only if entered) and sets the default provider — mirroring the
 * Settings provider/key flow without adding any IPC. It NEVER gates the
 * wizard: Finish/Skip work untouched, and a providers/save failure degrades to
 * an inline "set this up later in Settings" message.
 */
function buildChapterSection(isDisposed: () => boolean): ChapterSection {
  const providerSelect = el('select', { attrs: { id: 'setup-chapter-provider' } })
  const docsLink = el('a', { class: 'button-link chapter-docs', attrs: { rel: 'noreferrer' } })
  const customUrlInput = el('input', {
    attrs: {
      type: 'text',
      id: 'setup-chapter-custom-url',
      placeholder: 'https://… (or http on localhost)'
    }
  })
  const customUrlField = el(
    'div',
    { class: 'field' },
    el('label', { text: 'Custom provider base URL', attrs: { for: 'setup-chapter-custom-url' } }),
    customUrlInput
  )
  // Hidden until the providers load resolves the selected provider; only the
  // legacy `custom` slot reveals it.
  customUrlField.hidden = true
  const keyInput = el('input', {
    attrs: { type: 'password', autocomplete: 'off', id: 'setup-chapter-key', placeholder: 'no key set' }
  })
  const testButton = el('button', {
    text: 'Test',
    class: 'button-secondary',
    attrs: { type: 'button' }
  })
  const saveButton = el('button', {
    text: 'Save & continue',
    attrs: { type: 'button', id: 'setup-chapter-save' }
  })
  const keyResult = el('span', { class: 'key-result chapter-result', attrs: { role: 'status' } })

  const benefit = (lead: string, rest: string): HTMLElement =>
    el(
      'li',
      { class: 'chapter-benefit' },
      el('span', { class: 'chapter-tick', text: '✓', attrs: { 'aria-hidden': 'true' } }),
      el('div', {}, el('strong', { text: lead }), document.createTextNode(` — ${rest}`))
    )

  const section = el(
    'section',
    { class: 'setup-section chapter-section' },
    el(
      'div',
      { class: 'chapter-head' },
      el('h3', { class: 'setup-section-title', text: 'Make it smarter with an AI model' }),
      el('span', { class: 'chapter-optional', text: 'Optional' })
    ),
    el('p', {
      class: 'setup-section-note',
      text: 'Transcription already works on its own. Connect an AI language model and your transcripts gain:'
    }),
    el(
      'ul',
      { class: 'chapter-benefits' },
      benefit('Chapter markers', 'jump to topics, with a summary per section.'),
      benefit('Real paragraphs', 'grouped by idea, not chopped by sentence count.'),
      benefit('Key points & pull quotes', 'surfaced as you read.')
    ),
    el(
      'div',
      { class: 'field' },
      el('label', { text: 'Provider', attrs: { for: 'setup-chapter-provider' } }),
      providerSelect,
      docsLink
    ),
    customUrlField,
    el(
      'div',
      { class: 'field' },
      el('label', { text: 'API key', attrs: { for: 'setup-chapter-key' } }),
      keyInput,
      el('div', { class: 'key-actions' }, testButton, keyResult)
    ),
    el('p', {
      class: 'chapter-reassure',
      text:
        "Stored encrypted on this device. It's sent only to the provider you choose, " +
        'never to us.'
    }),
    el('div', { class: 'form-actions chapter-actions' }, saveButton)
  )

  let providers: ProviderInfo[] = []
  let degraded = false

  function degrade(message: string): void {
    degraded = true
    keyResult.textContent = message
  }

  function renderOptions(selected: string): void {
    providerSelect.replaceChildren()
    for (const provider of providers) {
      const label = provider.id + (provider.key_available ? ' • key set' : '')
      providerSelect.append(el('option', { text: label, attrs: { value: provider.id } }))
    }
    if (providers.some((p) => p.id === selected)) providerSelect.value = selected
  }

  function syncProviderUi(): void {
    const provider = providerSelect.value
    customUrlField.hidden = !customUrlVisible(provider)
    keyInput.placeholder = keyPlaceholder(providers, provider)
    const url = providerDocsUrl(provider)
    if (url === null) {
      docsLink.hidden = true
      docsLink.removeAttribute('href')
      docsLink.textContent = ''
    } else {
      // A real link: the main process routes target="_blank" http(s) opens to
      // the OS default browser (where the user is signed in), never an in-app
      // window.
      docsLink.hidden = false
      docsLink.setAttribute('href', url)
      docsLink.setAttribute('target', '_blank')
      docsLink.textContent = 'How do I get a key?'
    }
    keyResult.textContent = ''
  }

  providerSelect.addEventListener('change', syncProviderUi)

  testButton.addEventListener('click', () => {
    if (degraded || providers.length === 0) return
    testButton.disabled = true
    keyResult.textContent = 'Testing…'
    const entered = keyInput.value
    window.api
      .testKey(providerSelect.value, entered === '' ? undefined : entered)
      .then(async (result) => {
        if (!result.ok) {
          keyResult.textContent = `Key test failed: ${result.detail ?? 'unknown error'}`
          return
        }
        // A working key the user just entered is one they want to use: persist
        // it (and set it as the chapter provider) right away rather than
        // stranding them at "Key works." with nothing saved. Testing the
        // already-stored key (empty field) has nothing to persist.
        if (entered === '') {
          keyResult.textContent = 'Saved key works.'
          return
        }
        // Persist in its own try/catch: a save failure must NOT be reported as
        // a test failure (the key is valid), and must be surfaced — silently
        // dropping a validated key is the exact bug this auto-save fixes.
        try {
          await persistChapterConfig(entered)
        } catch (err) {
          if (isDisposed()) return
          keyResult.textContent = `Key works, but saving it failed: ${extractEngineDetail(err)}`
          return
        }
        if (isDisposed()) return
        keyInput.value = ''
        keyResult.textContent = 'Key works — saved and set as your chapter provider.'
      })
      .catch((err: unknown) => {
        if (isDisposed()) return
        keyResult.textContent = `Key test failed: ${extractEngineDetail(err)}`
      })
      .finally(() => {
        testButton.disabled = false
      })
  })

  // Persist the chosen provider (+ custom URL) and, if a key was entered, push
  // it — the one routing shared by Test-on-success and the Save button.
  async function persistChapterConfig(key: string): Promise<void> {
    const plan = planChapterSave({
      provider: providerSelect.value,
      key,
      customUrl: customUrlInput.value
    })
    const settings = await window.api.getSettings()
    await window.api.putSettings({ ...settings, ...plan.settings })
    if (plan.key !== null) await window.api.putKey(plan.key.provider, plan.key.value)
  }

  saveButton.addEventListener('click', () => {
    // Don't submit when the section never loaded its providers (degraded /
    // transient failure): the provider select would be empty/stale (cubic).
    if (degraded || providers.length === 0) return
    saveButton.disabled = true
    keyResult.textContent = 'Saving…'
    void (async () => {
      try {
        await persistChapterConfig(keyInput.value)
        keyInput.value = ''
        if (isDisposed()) return
        keyResult.textContent = 'Saved.'
      } catch (err) {
        if (isDisposed()) return
        keyResult.textContent = extractEngineDetail(err)
      } finally {
        saveButton.disabled = false
      }
    })()
  })

  async function load(): Promise<void> {
    // Re-attemptable: a transient failure must NOT permanently disable the
    // section (cubic) — load() is re-invoked on engine-ready/hydration, so it
    // never early-returns on `degraded`; a later success recovers it.
    if (isDisposed()) return
    try {
      const [providerList, settings] = await Promise.all([
        window.api.listProviders(),
        window.api.getSettings()
      ])
      if (isDisposed()) return
      providers = providerList
      degraded = false // recovered: re-enable Save/Test, clear the degrade note
      renderOptions(settings.chapter_provider)
      customUrlInput.value = settings.custom_provider_url
      syncProviderUi() // sets keyResult back to '' (clears any degrade message)
    } catch {
      // Degrade gracefully — never trap the wizard (design error handling).
      degrade('AI setup is unavailable right now — you can set this up later in Settings.')
    }
  }

  return { section, load }
}
