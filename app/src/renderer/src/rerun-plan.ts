import type { JobOverrides } from '../../shared/types'

/**
 * Pure builder for a rerun's model overrides (rerun dialog). Two opt-in
 * sections — re-transcribe (Whisper model) and regenerate-chapters (provider +
 * model) — map to exactly the override fields the engine uses to decide what
 * cached artifacts to clear. Kept DOM-free so the routing is unit-tested.
 */
export interface RerunInput {
  reTranscribe: boolean
  whisperModel: string
  reChapter: boolean
  chapterProvider: string
  chapterModel: string
  customUrl: string
}

export interface RerunPlan {
  overrides: JobOverrides
  /** False when neither section is enabled (or a required field is blank). */
  valid: boolean
}

export function buildRerunOverrides(input: RerunInput): RerunPlan {
  const overrides: JobOverrides = {}
  if (input.reTranscribe && input.whisperModel.trim() !== '') {
    overrides.whisper_model = input.whisperModel.trim()
  }
  if (input.reChapter && input.chapterProvider !== '') {
    overrides.chapter_provider = input.chapterProvider
    if (input.chapterModel.trim() !== '') overrides.chapter_model = input.chapterModel.trim()
    if (input.chapterProvider === 'custom' && input.customUrl.trim() !== '') {
      overrides.custom_provider_url = input.customUrl.trim()
    }
  }
  const valid =
    overrides.whisper_model !== undefined || overrides.chapter_provider !== undefined
  return { overrides, valid }
}
