import { describe, expect, it } from 'vitest'

import { formFromSettings, keyPlaceholder, modelPlaceholder, toSettingsUpdate } from './settings-form'
import type { EngineSettings, ProviderInfo } from '../../shared/types'

const settings: EngineSettings = {
  whisper_model: 'large-v3',
  whisper_lang: 'en',
  whisper_device: 'cuda',
  sentences: 5,
  library_dir: '/home/jim/PodcastReader/library',
  chapter_model: '',
  chapter_provider: 'anthropic',
  custom_provider_url: ''
}

const providers: ProviderInfo[] = [
  { id: 'anthropic', default_model: 'claude-sonnet-4-5', key_available: true },
  { id: 'custom', default_model: '', key_available: false }
]

describe('formFromSettings', () => {
  it('mirrors engine settings into string form values', () => {
    expect(formFromSettings(settings)).toEqual({
      whisper_model: 'large-v3',
      whisper_lang: 'en',
      whisper_device: 'cuda',
      sentences: '5',
      library_dir: '/home/jim/PodcastReader/library',
      chapter_model: '',
      chapter_provider: 'anthropic',
      custom_provider_url: ''
    })
  })
})

describe('toSettingsUpdate', () => {
  it('round-trips form values into a PUT /v1/settings body', () => {
    expect(toSettingsUpdate(formFromSettings(settings))).toEqual({
      ok: true,
      update: {
        whisper_model: 'large-v3',
        whisper_lang: 'en',
        whisper_device: 'cuda',
        sentences: 5,
        library_dir: '/home/jim/PodcastReader/library',
        chapter_model: '',
        chapter_provider: 'anthropic',
        custom_provider_url: ''
      }
    })
  })

  it('rejects a non-positive or non-numeric sentences value client-side', () => {
    for (const bad of ['0', '-2', 'abc', '', '1.5']) {
      const result = toSettingsUpdate({ ...formFromSettings(settings), sentences: bad })
      expect(result).toEqual({
        ok: false,
        field: 'sentences',
        message: 'sentences must be a positive whole number'
      })
    }
  })

  it('trims whitespace from text fields', () => {
    const result = toSettingsUpdate({
      ...formFromSettings(settings),
      whisper_model: ' base ',
      custom_provider_url: ' https://llm.local/v1 '
    })
    expect(result.ok).toBe(true)
    if (result.ok) {
      expect(result.update.whisper_model).toBe('base')
      expect(result.update.custom_provider_url).toBe('https://llm.local/v1')
    }
  })
})

describe('modelPlaceholder', () => {
  it('names the selected provider default model', () => {
    expect(modelPlaceholder(providers, 'anthropic')).toBe('default: claude-sonnet-4-5')
  })

  it('falls back when the provider has no default', () => {
    expect(modelPlaceholder(providers, 'custom')).toBe('provider default')
    expect(modelPlaceholder(providers, 'unknown')).toBe('provider default')
  })
})

describe('keyPlaceholder', () => {
  it('signals a configured key without ever showing it', () => {
    expect(keyPlaceholder(providers, 'anthropic')).toBe('configured — enter a new key to replace')
  })

  it('signals a missing key', () => {
    expect(keyPlaceholder(providers, 'custom')).toBe('no key set')
    expect(keyPlaceholder(providers, 'unknown')).toBe('no key set')
  })
})
