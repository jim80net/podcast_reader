/**
 * Pure helpers for the first-run chapter-provider onboarding section
 * (wizard-chapter-provider design). Kept framework-free and DOM-free so the
 * provider→docs map, the custom-URL toggle, and the save-routing plan can be
 * unit-tested in the node vitest environment (the renderer's DOM is covered by
 * the Playwright e2e suite, mirroring how Settings is tested).
 */

/**
 * Where a novice gets an API key per provider. A missing entry (e.g. `custom`
 * or an unknown id) yields `null` — we render no link rather than a broken
 * one, per the design's "links rot" mitigation.
 */
export const PROVIDER_DOCS_URL: Readonly<Record<string, string>> = {
  anthropic: 'https://console.anthropic.com/settings/keys',
  openai: 'https://platform.openai.com/api-keys',
  xai: 'https://console.x.ai',
  openrouter: 'https://openrouter.ai/keys',
  deepseek: 'https://platform.deepseek.com'
}

export function providerDocsUrl(providerId: string): string | null {
  return PROVIDER_DOCS_URL[providerId] ?? null
}

/** The base-URL field is shown only for the legacy single `custom` slot. */
export function customUrlVisible(providerId: string): boolean {
  return providerId === 'custom'
}

export interface ChapterSaveInput {
  provider: string
  /** Raw key-input value; an empty string means "no new key entered". */
  key: string
  /** Raw custom-URL value (only meaningful when provider === 'custom'). */
  customUrl: string
}

/**
 * What a Save should do, separated from the IPC calls so the routing is
 * testable: always persist the chosen provider (+ custom URL) via
 * `putSettings`, and push the key via `putKey` ONLY when one was entered
 * (keys are write-only and never read back, per the Settings convention).
 */
export interface ChapterSavePlan {
  settings: { chapter_provider: string; custom_provider_url: string }
  key: { provider: string; value: string } | null
}

export function planChapterSave(input: ChapterSaveInput): ChapterSavePlan {
  // Trim the key (like customUrl): a whitespace-only entry is "no key", not a
  // bogus key that would save and then fail auth (OCR).
  const key = input.key.trim()
  return {
    settings: {
      chapter_provider: input.provider,
      custom_provider_url: input.customUrl.trim()
    },
    key: key === '' ? null : { provider: input.provider, value: key }
  }
}
