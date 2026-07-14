import { mountCookiesSection } from './cookies-section'
import { mountPacksSection } from './packs-section'
import { mountPairingSection } from './pairing-section'
import { planChapterSave } from '../chapter-onboarding'
import { el } from '../dom'
import { extractEngineDetail, settingsErrorField } from '../engine-error'
import { keyPlaceholder, modelPlaceholder, toSettingsUpdate } from '../settings-form'
import {
  normalizeNamedProviderKey,
  removeNamedProvider,
  toNamedProviderConfig,
  upsertNamedProvider
} from '../named-provider-form'
import type { CookiesSection } from './cookies-section'
import type { PacksSection } from './packs-section'
import type { PairingSection } from './pairing-section'
import type { SettingsFormValues } from '../settings-form'
import type { ViewCleanup } from '../store'
import type { CustomProviderConfig, EngineSettings, ProviderInfo } from '../../../shared/types'

/**
 * Settings view (app-views spec, task 4.6): provider dropdown fed by
 * `GET /v1/providers` (single registry home, per P4), write-only masked key
 * entry with engine-side key test, whisper/sentences/library-dir fields, and
 * a save that PUTs settings then routes key changes through the
 * vault-and-push flow. Engine validation errors land inline next to the
 * offending field; safeStorage-unavailable mode shows a visible warning.
 */
export function mountSettings(container: HTMLElement): ViewCleanup {
  const status = el('p', { class: 'view-status', text: 'Loading settings…' })
  container.append(el('h2', { text: 'Settings' }), status)

  let disposed = false
  let loaded = false
  let packsSection: PacksSection | null = null
  let pairingSection: PairingSection | null = null
  let cookiesSection: CookiesSection | null = null

  async function load(): Promise<void> {
    try {
      const [settings, providers, storageMode] = await Promise.all([
        window.api.getSettings(),
        window.api.listProviders(),
        window.api.keyStorageMode()
      ])
      if (disposed || loaded) return
      loaded = true
      status.remove()
      renderForm(settings, providers, storageMode)
    } catch (err) {
      if (disposed) return
      status.textContent = `Settings unavailable: ${extractEngineDetail(err)}`
      status.classList.add('error-text')
    }
  }

  function renderForm(
    settings: EngineSettings,
    providers: ProviderInfo[],
    storageMode: 'encrypted' | 'session-memory'
  ): void {
    let knownProviders = providers
    let customProviders = settings.custom_providers.map((provider) => ({ ...provider }))
    const fieldErrors = new Map<string, HTMLElement>()

    function field(
      id: string,
      label: string,
      input: HTMLInputElement | HTMLSelectElement
    ): HTMLElement {
      input.id = `settings-${id}`
      const error = el('p', { class: 'error-text field-error', attrs: { role: 'alert' } })
      error.hidden = true
      fieldErrors.set(id, error)
      return el(
        'div',
        { class: 'field' },
        el('label', { text: label, attrs: { for: input.id } }),
        input,
        error
      )
    }

    function clearErrors(): void {
      for (const error of fieldErrors.values()) {
        error.hidden = true
        error.textContent = ''
      }
      generalError.hidden = true
      saveStatus.textContent = ''
    }

    function showFieldError(fieldId: string | null, message: string): void {
      const target = fieldId === null ? null : (fieldErrors.get(fieldId) ?? null)
      if (target === null) {
        generalError.textContent = message
        generalError.hidden = false
        return
      }
      target.textContent = message
      target.hidden = false
    }

    // -- chapter provider -----------------------------------------------
    const providerSelect = el('select', {})
    function renderProviderOptions(selected: string): void {
      providerSelect.replaceChildren()
      for (const provider of knownProviders) {
        const label =
          provider.id +
          (provider.default_model !== '' ? ` (${provider.default_model})` : '') +
          (provider.key_available ? ' • key set' : '')
        providerSelect.append(
          el('option', { text: label, attrs: { value: provider.id } })
        )
      }
      providerSelect.value = selected
    }
    renderProviderOptions(settings.chapter_provider)

    const customUrlInput = el('input', {
      attrs: { type: 'text', placeholder: 'https://… (or http on localhost)' }
    })
    customUrlInput.value = settings.custom_provider_url
    const customUrlField = field('custom_provider_url', 'Custom provider base URL', customUrlInput)

    const modelInput = el('input', { attrs: { type: 'text' } })
    modelInput.value = settings.chapter_model
    const cleanupInput = el('input', {
      attrs: { type: 'checkbox', id: 'settings-caption-cleanup' }
    })
    cleanupInput.checked = settings.caption_cleanup
    const cleanupField = el(
      'div',
      { class: 'field' },
      el(
        'label',
        { class: 'checkbox-label', attrs: { for: cleanupInput.id } },
        cleanupInput,
        el('span', { text: 'Clean up caption spelling and casing' })
      ),
      el('p', {
        class: 'field-note',
        text:
          'Optional. Uses the chapter model and labels the transcript. Only validated ' +
          'single-word spelling/casing fixes are applied; wording and source captions stay intact.'
      })
    )

    // -- API key (write-only; never read back) ---------------------------
    const keyInput = el('input', {
      attrs: { type: 'password', autocomplete: 'off' }
    })
    const keyTestButton = el('button', {
      text: 'Test key',
      class: 'button-secondary',
      attrs: { type: 'button' }
    })
    const keyClearButton = el('button', {
      text: 'Clear stored key',
      class: 'button-secondary',
      attrs: { type: 'button' }
    })
    const keyResult = el('span', { class: 'key-result', attrs: { role: 'status' } })

    function syncProviderDependentUi(): void {
      const provider = providerSelect.value
      customUrlField.hidden = provider !== 'custom'
      modelInput.placeholder = modelPlaceholder(knownProviders, provider)
      keyInput.placeholder = keyPlaceholder(knownProviders, provider)
      keyResult.textContent = ''
    }
    syncProviderDependentUi()
    let previousProvider = providerSelect.value
    providerSelect.addEventListener('change', () => {
      if (providerSelect.value !== previousProvider) modelInput.value = ''
      previousProvider = providerSelect.value
      syncProviderDependentUi()
    })

    async function refreshProviders(): Promise<void> {
      try {
        knownProviders = await window.api.listProviders()
        if (disposed) return
        renderProviderOptions(providerSelect.value)
        syncProviderDependentUi()
      } catch {
        // cosmetic only — stale key-availability labels until next visit
      }
    }

    keyTestButton.addEventListener('click', () => {
      setSettingsMutationBusy(true)
      keyResult.textContent = 'Testing…'
      // Snapshot at click time: the dropdown could change during the async test
      // round-trip, and the key must persist under the provider we tested, not
      // whatever is selected when the promise resolves (cubic).
      const entered = keyInput.value
      const provider = providerSelect.value
      const customUrl = customUrlInput.value
      window.api
        .testKey(provider, entered === '' ? undefined : entered)
        .then(async (result) => {
          if (!result.ok) {
            keyResult.textContent = `Key test failed: ${result.detail ?? 'unknown error'}`
            return
          }
          // A working key the user just entered is one they want to use:
          // persist it (and set it as the chapter provider) immediately rather
          // than leaving them at "Key works." with nothing saved. Testing the
          // already-stored key (empty field) has nothing to persist.
          if (entered === '') {
            keyResult.textContent = 'Saved key works.'
            return
          }
          // Persist in its own try/catch: a save failure must NOT be reported
          // as a test failure (the key is valid), and must be surfaced — never
          // silently drop a validated key.
          try {
            const plan = planChapterSave({ provider, key: entered, customUrl })
            const current = await window.api.getSettings()
            await window.api.putSettings({ ...current, ...plan.settings })
            if (plan.key !== null) await window.api.putKey(plan.key.provider, plan.key.value)
            keyInput.value = ''
            await refreshProviders()
          } catch (err) {
            if (disposed) return
            keyResult.textContent = `Key works, but saving it failed: ${extractEngineDetail(err)}`
            return
          }
          if (disposed) return
          keyResult.textContent = 'Key works — saved and set as your chapter provider.'
        })
        .catch((err: unknown) => {
          if (disposed) return
          keyResult.textContent = `Key test failed: ${extractEngineDetail(err)}`
        })
        .finally(() => {
          setSettingsMutationBusy(false)
        })
    })

    keyClearButton.addEventListener('click', () => {
      setSettingsMutationBusy(true)
      // "" clears the vault entry and restores the engine's env fallback.
      window.api
        .putKey(providerSelect.value, '')
        .then(() => {
          keyResult.textContent = 'Stored key cleared.'
          keyInput.value = ''
          return refreshProviders()
        })
        .catch((err: unknown) => {
          keyResult.textContent = `Clear failed: ${extractEngineDetail(err)}`
        })
        .finally(() => {
          setSettingsMutationBusy(false)
        })
    })

    // -- user-defined providers (nonsecret configuration) ----------------
    const namedSelect = el('select', {})
    const namedNameInput = el('input', {
      attrs: { type: 'text', placeholder: 'office-gateway' }
    })
    const namedUrlInput = el('input', {
      attrs: { type: 'text', placeholder: 'https://llm.example/v1' }
    })
    const namedModelInput = el('input', { attrs: { type: 'text', placeholder: 'model-name' } })
    const namedTokensInput = el('input', {
      attrs: { type: 'number', min: '1', max: '1000000', step: '1' }
    })
    const namedKeyInput = el('input', {
      attrs: { type: 'password', autocomplete: 'off' }
    })
    const namedEditor = el('div', { class: 'settings-form' })
    const namedStatus = el('span', { class: 'key-result', attrs: { role: 'status' } })
    const addNamedButton = el('button', {
      text: 'Add provider',
      class: 'button-secondary',
      attrs: { type: 'button' }
    })
    const saveNamedButton = el('button', {
      text: 'Save provider',
      class: 'button-secondary',
      attrs: { type: 'button' }
    })
    const removeNamedButton = el('button', {
      text: 'Remove provider',
      class: 'button-secondary',
      attrs: { type: 'button' }
    })
    const testNamedButton = el('button', {
      text: 'Save provider and test key',
      class: 'button-secondary',
      attrs: { type: 'button' }
    })
    let editingName: string | undefined

    const namedControls = [
      namedSelect,
      namedNameInput,
      namedUrlInput,
      namedModelInput,
      namedTokensInput,
      namedKeyInput,
      addNamedButton,
      saveNamedButton,
      testNamedButton,
      removeNamedButton
    ]

    function setSettingsMutationBusy(busy: boolean): void {
      for (const control of [
        ...namedControls,
        providerSelect,
        keyTestButton,
        keyClearButton,
        saveButton
      ]) {
        control.disabled = busy
      }
      if (!busy) namedNameInput.disabled = editingName !== undefined
    }

    function renderNamedOptions(selected?: string): void {
      namedSelect.replaceChildren(
        el('option', { text: 'Choose a provider to edit', attrs: { value: '' } })
      )
      for (const provider of customProviders) {
        namedSelect.append(el('option', { text: provider.name, attrs: { value: provider.name } }))
      }
      namedSelect.value = selected ?? ''
    }

    function showNamedEditor(provider?: CustomProviderConfig): void {
      editingName = provider?.name
      namedNameInput.value = provider?.name ?? ''
      namedNameInput.disabled = provider !== undefined
      namedUrlInput.value = provider?.base_url ?? ''
      namedModelInput.value = provider?.default_model ?? ''
      namedTokensInput.value = provider === undefined ? '16384' : String(provider.max_tokens)
      namedKeyInput.value = ''
      namedKeyInput.placeholder =
        provider === undefined ? 'enter a key to test' : keyPlaceholder(knownProviders, provider.name)
      namedEditor.hidden = false
      removeNamedButton.hidden = provider === undefined
      namedStatus.textContent = ''
    }

    renderNamedOptions()
    namedEditor.append(
      field('named_provider_name', 'Provider name', namedNameInput),
      field('named_provider_url', 'Base URL', namedUrlInput),
      field('named_provider_model', 'Provider default model', namedModelInput),
      field('named_provider_tokens', 'Maximum output tokens', namedTokensInput),
      field('named_provider_key', 'API key (write-only)', namedKeyInput),
      el(
        'div',
        { class: 'key-actions' },
        saveNamedButton,
        testNamedButton,
        removeNamedButton,
        namedStatus
      )
    )
    namedEditor.hidden = true
    namedSelect.addEventListener('change', () => {
      const provider = customProviders.find((entry) => entry.name === namedSelect.value)
      if (provider === undefined) {
        namedEditor.hidden = true
        return
      }
      showNamedEditor(provider)
    })
    addNamedButton.addEventListener('click', () => {
      renderNamedOptions()
      showNamedEditor()
      namedNameInput.focus()
    })

    saveNamedButton.addEventListener('click', () => {
      clearErrors()
      const result = toNamedProviderConfig(
        {
          name: namedNameInput.value,
          base_url: namedUrlInput.value,
          default_model: namedModelInput.value,
          max_tokens: namedTokensInput.value
        },
        customProviders,
        editingName
      )
      if (!result.ok) {
        showFieldError(`named_provider_${result.field.replace('base_url', 'url').replace('default_model', 'model').replace('max_tokens', 'tokens')}`, result.message)
        return
      }
      const next = upsertNamedProvider(customProviders, result.config, editingName)
      const enteredKey = normalizeNamedProviderKey(namedKeyInput.value)
      setSettingsMutationBusy(true)
      void (async () => {
        try {
          const current = await window.api.getSettings()
          const saved = await window.api.putSettings({ ...current, custom_providers: next })
          customProviders = saved.custom_providers.map((provider) => ({ ...provider }))
          Object.assign(settings, saved)
          editingName = result.config.name
          renderNamedOptions(editingName)
          showNamedEditor(result.config)
          await refreshProviders()
          if (enteredKey !== undefined) {
            try {
              await window.api.putKey(result.config.name, enteredKey)
              await refreshProviders()
              namedStatus.textContent = 'Provider and key saved.'
            } catch (err) {
              namedKeyInput.value = enteredKey
              namedStatus.textContent =
                `Provider saved, but saving its key failed: ${extractEngineDetail(err)}`
            }
          } else {
            namedStatus.textContent =
              'Provider saved. Select it above to add or test its write-only API key.'
          }
        } catch (err) {
          namedStatus.textContent = `Save failed: ${extractEngineDetail(err)}`
        } finally {
          setSettingsMutationBusy(false)
        }
      })()
    })

    testNamedButton.addEventListener('click', () => {
      clearErrors()
      const result = toNamedProviderConfig(
        {
          name: namedNameInput.value,
          base_url: namedUrlInput.value,
          default_model: namedModelInput.value,
          max_tokens: namedTokensInput.value
        },
        customProviders,
        editingName
      )
      if (!result.ok) {
        showFieldError(
          `named_provider_${result.field.replace('base_url', 'url').replace('default_model', 'model').replace('max_tokens', 'tokens')}`,
          result.message
        )
        return
      }
      const next = upsertNamedProvider(customProviders, result.config, editingName)
      const enteredKey = normalizeNamedProviderKey(namedKeyInput.value)
      setSettingsMutationBusy(true)
      namedStatus.textContent = 'Saving provider and testing…'
      void (async () => {
        try {
          const current = await window.api.getSettings()
          const saved = await window.api.putSettings({ ...current, custom_providers: next })
          customProviders = saved.custom_providers.map((provider) => ({ ...provider }))
          Object.assign(settings, saved)
          editingName = result.config.name
          renderNamedOptions(editingName)
          await refreshProviders()
          const tested = await window.api.testKey(
            result.config.name,
            enteredKey
          )
          if (!tested.ok) {
            namedStatus.textContent =
              `Provider saved; key test failed: ${tested.detail ?? 'unknown error'}`
            return
          }
          if (enteredKey !== undefined) {
            await window.api.putKey(result.config.name, enteredKey)
            namedKeyInput.value = ''
            await refreshProviders()
          }
          namedStatus.textContent =
            enteredKey === undefined
              ? 'Provider saved; stored key works.'
              : 'Provider and key saved.'
        } catch (err) {
          namedStatus.textContent = `Save or test failed: ${extractEngineDetail(err)}`
        } finally {
          setSettingsMutationBusy(false)
          removeNamedButton.hidden = editingName === undefined
        }
      })()
    })

    removeNamedButton.addEventListener('click', () => {
      if (editingName === undefined) return
      const removedName = editingName
      const next = removeNamedProvider(customProviders, removedName)
      setSettingsMutationBusy(true)
      void (async () => {
        try {
          const current = await window.api.getSettings()
          const replacement =
            current.chapter_provider === removedName ? 'anthropic' : current.chapter_provider
          const saved = await window.api.putSettings({
            ...current,
            chapter_provider: replacement,
            chapter_model: replacement === current.chapter_provider ? current.chapter_model : '',
            custom_providers: next
          })
          customProviders = saved.custom_providers.map((provider) => ({ ...provider }))
          Object.assign(settings, saved)
          providerSelect.value = saved.chapter_provider
          modelInput.value = saved.chapter_model
          previousProvider = saved.chapter_provider
          editingName = undefined
          renderNamedOptions()
          namedEditor.hidden = true
          await refreshProviders()
          try {
            await window.api.putKey(removedName, '')
            saveStatus.textContent = 'Provider and stored key removed.'
          } catch (err) {
            saveStatus.textContent =
              `Provider removed, but clearing its stored key failed: ${extractEngineDetail(err)}`
          }
        } catch (err) {
          namedStatus.textContent = `Remove failed: ${extractEngineDetail(err)}`
        } finally {
          setSettingsMutationBusy(false)
        }
      })()
    })

    // -- whisper / output -------------------------------------------------
    const whisperModelInput = el('input', { attrs: { type: 'text' } })
    whisperModelInput.value = settings.whisper_model
    const deviceSelect = el('select', {})
    for (const device of ['cuda', 'cpu']) {
      deviceSelect.append(el('option', { text: device, attrs: { value: device } }))
    }
    if (!['cuda', 'cpu'].includes(settings.whisper_device)) {
      deviceSelect.append(
        el('option', { text: settings.whisper_device, attrs: { value: settings.whisper_device } })
      )
    }
    deviceSelect.value = settings.whisper_device
    deviceSelect.addEventListener('change', () => packsSection?.deviceChanged())
    const langInput = el('input', { attrs: { type: 'text' } })
    langInput.value = settings.whisper_lang
    const sentencesInput = el('input', { attrs: { type: 'number', min: '1', step: '1' } })
    sentencesInput.value = String(settings.sentences)
    // The setting key stays `sentences`; the label and note explain that this
    // length is only the fallback when no AI chapter model groups paragraphs.
    const sentencesField = field('sentences', 'Fallback paragraph length', sentencesInput)
    sentencesField.append(
      el('p', {
        class: 'field-note',
        text:
          'Used only when no AI chapter model is set. With an AI model, ' +
          'paragraphs follow the actual ideas.'
      })
    )
    const libraryDirInput = el('input', { attrs: { type: 'text' } })
    libraryDirInput.value = settings.library_dir

    // -- save -------------------------------------------------------------
    // The submit button lives in a sticky action bar pinned to the bottom of
    // the page (below), not sandwiched inside the form between the Output
    // fields and the Packs section — so `form="settings-form"` ties the
    // out-of-form button back to the form it submits.
    const saveButton = el('button', {
      text: 'Save',
      attrs: { type: 'submit', form: 'settings-form' }
    })
    const saveStatus = el('span', { class: 'key-result', attrs: { role: 'status' } })
    const generalError = el('p', { class: 'error-text', attrs: { role: 'alert' } })
    generalError.hidden = true

    const form = el(
      'form',
      { class: 'settings-form', attrs: { id: 'settings-form' } },
      el('h3', { text: 'Chapters' }),
      field('chapter_provider', 'Provider', providerSelect),
      customUrlField,
      field('chapter_model', 'Chapter model override', modelInput),
      cleanupField,
      el(
        'div',
        { class: 'field' },
        el('label', { text: 'API key', attrs: { for: 'settings-api-key' } }),
        keyInput,
        el('div', { class: 'key-actions' }, keyTestButton, keyClearButton, keyResult)
      ),
      el('h3', { text: 'User-defined providers' }),
      el('p', {
        class: 'field-note',
        text:
          'Add OpenAI-compatible endpoints here. Configuration is saved without credentials; ' +
          'keys use the write-only field above after you select the provider.'
      }),
      field('named_provider_select', 'Saved providers', namedSelect),
      addNamedButton,
      namedEditor,
      el('h3', { text: 'Transcription' }),
      field('whisper_model', 'Whisper model', whisperModelInput),
      field('whisper_device', 'Device', deviceSelect),
      field('whisper_lang', 'Language', langInput),
      el('h3', { text: 'Output' }),
      sentencesField,
      field('library_dir', 'Library directory', libraryDirInput),
      generalError
    )
    keyInput.id = 'settings-api-key'

    if (storageMode === 'session-memory') {
      container.append(
        el('p', {
          class: 'banner warning-banner',
          text:
            'OS-level encryption is unavailable: API keys are kept in memory ' +
            'for this session only and will need re-entering after a restart.',
          attrs: { role: 'alert' }
        })
      )
    }
    container.append(form)
    syncProviderDependentUi()

    // Packs management below the form (task 6.3). The advisory follows the
    // device select live; the saved value seeds it (per S4/Q2).
    const packsContainer = el('section', { class: 'packs-section' })
    container.append(packsContainer)
    packsSection = mountPacksSection(packsContainer, () => deviceSelect.value)

    // Extension pairing + captured-login management (chrome-extension change,
    // task 3.2): Settings sections, not new views (design decision 11).
    const pairingContainer = el('section', { class: 'pairing-section' })
    const cookiesContainer = el('section', { class: 'cookies-section' })
    container.append(pairingContainer, cookiesContainer)
    pairingSection = mountPairingSection(pairingContainer)
    cookiesSection = mountCookiesSection(cookiesContainer)

    // Sticky action bar, pinned to the bottom of the page so Save is always
    // reachable and never buried between the form and the Packs section.
    container.append(el('div', { class: 'form-actions settings-actions' }, saveButton, saveStatus))

    form.addEventListener('submit', (event) => {
      event.preventDefault()
      clearErrors()
      const values: SettingsFormValues = {
        whisper_model: whisperModelInput.value,
        whisper_lang: langInput.value,
        whisper_device: deviceSelect.value,
        sentences: sentencesInput.value,
        library_dir: libraryDirInput.value,
        chapter_model: modelInput.value,
        chapter_provider: providerSelect.value,
        custom_provider_url: customUrlInput.value,
        caption_cleanup: cleanupInput.checked
      }
      const result = toSettingsUpdate(values)
      if (!result.ok) {
        showFieldError(result.field, result.message)
        return
      }
      setSettingsMutationBusy(true)
      void (async () => {
        try {
          // Engine settings first — a rejected PUT persists nothing.
          const saved = await window.api.putSettings({
            ...result.update,
            custom_providers: customProviders
          })
          Object.assign(settings, saved)
          const key = keyInput.value
          if (key !== '') {
            await window.api.putKey(providerSelect.value, key)
            keyInput.value = ''
            await refreshProviders()
          }
          if (disposed) return
          saveStatus.textContent = 'Saved.'
        } catch (err) {
          if (disposed) return
          const detail = extractEngineDetail(err)
          showFieldError(settingsErrorField(detail), detail)
        } finally {
          setSettingsMutationBusy(false)
        }
      })()
    })
  }

  void load()
  // The engine may not have been ready at mount; load once it is.
  const unsubscribeStatus = window.api.onEngineStatus((engineStatus) => {
    if (engineStatus.state === 'ready' && !loaded) void load()
  })

  return () => {
    disposed = true
    unsubscribeStatus()
    packsSection?.cleanup()
    pairingSection?.cleanup()
    cookiesSection?.cleanup()
  }
}
