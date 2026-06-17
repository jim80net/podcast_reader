import { describe, expect, it } from 'vitest'

import { buildRerunOverrides } from './rerun-plan'

const base = {
  reTranscribe: false,
  whisperModel: '',
  reChapter: false,
  chapterProvider: '',
  chapterModel: '',
  customUrl: ''
}

describe('buildRerunOverrides', () => {
  it('is invalid when neither section is enabled', () => {
    expect(buildRerunOverrides(base)).toEqual({ overrides: {}, valid: false })
  })

  it('includes only the Whisper model for a re-transcribe', () => {
    const plan = buildRerunOverrides({ ...base, reTranscribe: true, whisperModel: 'medium' })
    expect(plan).toEqual({ overrides: { whisper_model: 'medium' }, valid: true })
  })

  it('includes provider (+ model) for a chapter regen', () => {
    const plan = buildRerunOverrides({
      ...base,
      reChapter: true,
      chapterProvider: 'xai',
      chapterModel: 'grok-4'
    })
    expect(plan).toEqual({
      overrides: { chapter_provider: 'xai', chapter_model: 'grok-4' },
      valid: true
    })
  })

  it('adds the custom URL only for the custom provider', () => {
    expect(
      buildRerunOverrides({
        ...base,
        reChapter: true,
        chapterProvider: 'custom',
        customUrl: 'https://llm.local/v1'
      }).overrides
    ).toEqual({ chapter_provider: 'custom', custom_provider_url: 'https://llm.local/v1' })
    // Non-custom provider ignores any stray custom URL.
    expect(
      buildRerunOverrides({
        ...base,
        reChapter: true,
        chapterProvider: 'openai',
        customUrl: 'https://llm.local/v1'
      }).overrides
    ).toEqual({ chapter_provider: 'openai' })
  })

  it('combines both sections (full re-run)', () => {
    const plan = buildRerunOverrides({
      reTranscribe: true,
      whisperModel: 'large-v3',
      reChapter: true,
      chapterProvider: 'anthropic',
      chapterModel: '',
      customUrl: ''
    })
    expect(plan.valid).toBe(true)
    expect(plan.overrides).toEqual({ whisper_model: 'large-v3', chapter_provider: 'anthropic' })
  })

  it('the custom provider needs a base URL to be valid', () => {
    expect(
      buildRerunOverrides({ ...base, reChapter: true, chapterProvider: 'custom' }).valid
    ).toBe(false)
    expect(
      buildRerunOverrides({
        ...base,
        reChapter: true,
        chapterProvider: 'custom',
        customUrl: 'https://llm.local/v1'
      }).valid
    ).toBe(true)
  })

  it('an enabled re-transcribe with a blank model is not valid on its own', () => {
    expect(buildRerunOverrides({ ...base, reTranscribe: true, whisperModel: '   ' })).toEqual({
      overrides: {},
      valid: false
    })
  })
})
