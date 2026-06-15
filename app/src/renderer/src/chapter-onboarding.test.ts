import { describe, expect, it } from 'vitest'

import {
  PROVIDER_DOCS_URL,
  customUrlVisible,
  planChapterSave,
  providerDocsUrl
} from './chapter-onboarding'

describe('providerDocsUrl', () => {
  it('returns the documented key page for each built-in provider', () => {
    expect(providerDocsUrl('anthropic')).toBe('https://console.anthropic.com/settings/keys')
    expect(providerDocsUrl('openai')).toBe('https://platform.openai.com/api-keys')
    expect(providerDocsUrl('xai')).toBe('https://console.x.ai')
    expect(providerDocsUrl('openrouter')).toBe('https://openrouter.ai/keys')
    expect(providerDocsUrl('deepseek')).toBe('https://platform.deepseek.com')
  })

  it('has no entry for custom or unknown providers (no link rather than a broken one)', () => {
    expect(providerDocsUrl('custom')).toBeNull()
    expect(providerDocsUrl('something-else')).toBeNull()
    expect(PROVIDER_DOCS_URL.custom).toBeUndefined()
  })
})

describe('customUrlVisible', () => {
  it('reveals the base-URL field only for the custom provider', () => {
    expect(customUrlVisible('custom')).toBe(true)
    expect(customUrlVisible('anthropic')).toBe(false)
    expect(customUrlVisible('openai')).toBe(false)
  })
})

describe('planChapterSave', () => {
  it('always routes the provider + trimmed custom URL into a settings update', () => {
    const plan = planChapterSave({
      provider: 'custom',
      key: '',
      customUrl: '  https://llm.local/v1  '
    })
    expect(plan.settings).toEqual({
      chapter_provider: 'custom',
      custom_provider_url: 'https://llm.local/v1'
    })
  })

  it('skips the key push when no key was entered', () => {
    const plan = planChapterSave({ provider: 'anthropic', key: '', customUrl: '' })
    expect(plan.key).toBeNull()
  })

  it('routes an entered key to putKey under the chosen provider, verbatim', () => {
    const plan = planChapterSave({ provider: 'openai', key: 'sk-test-123', customUrl: '' })
    expect(plan.key).toEqual({ provider: 'openai', value: 'sk-test-123' })
    expect(plan.settings.chapter_provider).toBe('openai')
  })
})
