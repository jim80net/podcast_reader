import type { CustomProviderConfig } from '../../shared/types'

const BUILTIN_NAMES = new Set([
  'anthropic',
  'openai',
  'xai',
  'openrouter',
  'deepseek',
  'custom'
])
const NAME_PATTERN = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/

export interface NamedProviderDraft {
  name: string
  base_url: string
  default_model: string
  max_tokens: string
}

export type NamedProviderResult =
  | { ok: true; config: CustomProviderConfig }
  | { ok: false; field: keyof NamedProviderDraft; message: string }

export function toNamedProviderConfig(
  draft: NamedProviderDraft,
  existing: readonly CustomProviderConfig[],
  originalName?: string
): NamedProviderResult {
  const name = draft.name.trim()
  if (name.length > 63 || !NAME_PATTERN.test(name) || BUILTIN_NAMES.has(name)) {
    return {
      ok: false,
      field: 'name',
      message: 'use a lowercase name such as office-gateway (built-in names are reserved)'
    }
  }
  if (existing.some((provider) => provider.name === name && provider.name !== originalName)) {
    return { ok: false, field: 'name', message: 'a provider with this name already exists' }
  }
  const base_url = draft.base_url.trim()
  try {
    const parsed = new URL(base_url)
    const local = ['localhost', '127.0.0.1', '[::1]'].includes(parsed.hostname)
    if (
      (parsed.protocol !== 'https:' && !(parsed.protocol === 'http:' && local)) ||
      parsed.username !== '' ||
      parsed.password !== '' ||
      parsed.search !== '' ||
      parsed.hash !== ''
    ) {
      throw new Error('unsafe URL')
    }
  } catch {
    return {
      ok: false,
      field: 'base_url',
      message: 'use HTTPS, or HTTP on localhost, with no credentials, query, or fragment'
    }
  }
  const default_model = draft.default_model.trim()
  if (default_model === '' || default_model.length > 256) {
    return { ok: false, field: 'default_model', message: 'enter a model name (256 characters max)' }
  }
  const max_tokens = Number(draft.max_tokens.trim())
  if (!Number.isInteger(max_tokens) || max_tokens < 1 || max_tokens > 1_000_000) {
    return {
      ok: false,
      field: 'max_tokens',
      message: 'max tokens must be a whole number from 1 to 1,000,000'
    }
  }
  return { ok: true, config: { name, base_url, default_model, max_tokens } }
}

export function upsertNamedProvider(
  providers: readonly CustomProviderConfig[],
  config: CustomProviderConfig,
  originalName?: string
): CustomProviderConfig[] {
  const target = originalName ?? config.name
  const index = providers.findIndex((provider) => provider.name === target)
  if (index === -1) return [...providers, config]
  return providers.map((provider, current) => (current === index ? config : provider))
}

export function removeNamedProvider(
  providers: readonly CustomProviderConfig[],
  name: string
): CustomProviderConfig[] {
  return providers.filter((provider) => provider.name !== name)
}

export function normalizeNamedProviderKey(value: string): string | undefined {
  const trimmed = value.trim()
  return trimmed === '' ? undefined : trimmed
}
