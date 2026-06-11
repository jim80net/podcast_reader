import type { EngineSettings, ProviderInfo, SettingsUpdate } from '../../shared/types'

/**
 * Pure form-state helpers for the Settings view: engine settings ↔ string
 * form values, client-side validation that the engine cannot express
 * (sentences must already be an int at the Pydantic boundary), and the
 * write-only key-entry affordances (keys are never read back — placeholders
 * only signal availability, per P4/K4).
 */

export interface SettingsFormValues {
  whisper_model: string
  whisper_lang: string
  whisper_device: string
  sentences: string
  library_dir: string
  chapter_model: string
  chapter_provider: string
  custom_provider_url: string
}

export type SettingsUpdateResult =
  | { ok: true; update: SettingsUpdate }
  | { ok: false; field: keyof SettingsFormValues; message: string }

export function formFromSettings(settings: EngineSettings): SettingsFormValues {
  return {
    whisper_model: settings.whisper_model,
    whisper_lang: settings.whisper_lang,
    whisper_device: settings.whisper_device,
    sentences: String(settings.sentences),
    library_dir: settings.library_dir,
    chapter_model: settings.chapter_model,
    chapter_provider: settings.chapter_provider,
    custom_provider_url: settings.custom_provider_url
  }
}

export function toSettingsUpdate(values: SettingsFormValues): SettingsUpdateResult {
  const sentences = Number(values.sentences.trim())
  if (!Number.isInteger(sentences) || sentences <= 0 || values.sentences.trim() === '') {
    return { ok: false, field: 'sentences', message: 'sentences must be a positive whole number' }
  }
  return {
    ok: true,
    update: {
      whisper_model: values.whisper_model.trim(),
      whisper_lang: values.whisper_lang.trim(),
      whisper_device: values.whisper_device.trim(),
      sentences,
      library_dir: values.library_dir.trim(),
      chapter_model: values.chapter_model.trim(),
      chapter_provider: values.chapter_provider,
      custom_provider_url: values.custom_provider_url.trim()
    }
  }
}

export function modelPlaceholder(providers: readonly ProviderInfo[], providerId: string): string {
  const provider = providers.find((p) => p.id === providerId)
  if (provider === undefined || provider.default_model === '') return 'provider default'
  return `default: ${provider.default_model}`
}

export function keyPlaceholder(providers: readonly ProviderInfo[], providerId: string): string {
  const provider = providers.find((p) => p.id === providerId)
  return provider?.key_available === true
    ? 'configured — enter a new key to replace'
    : 'no key set'
}
