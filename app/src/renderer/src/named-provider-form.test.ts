import { describe, expect, it } from 'vitest'

import {
  normalizeNamedProviderKey,
  removeNamedProvider,
  toNamedProviderConfig,
  upsertNamedProvider
} from './named-provider-form'
import type { CustomProviderConfig } from '../../shared/types'

const office: CustomProviderConfig = {
  name: 'office-gateway',
  base_url: 'https://llm.corp.example/v1',
  default_model: 'corp-small',
  max_tokens: 32768
}

describe('toNamedProviderConfig', () => {
  it('canonicalizes a valid nonsecret draft', () => {
    expect(
      toNamedProviderConfig(
        {
          name: ' office-gateway ',
          base_url: ' https://llm.corp.example/v1 ',
          default_model: ' corp-small ',
          max_tokens: '32768'
        },
        []
      )
    ).toEqual({ ok: true, config: office })
  })

  it('rejects invalid, reserved, and duplicate names', () => {
    expect(
      toNamedProviderConfig({ ...office, name: 'Bad_Name', max_tokens: '10' }, [])
    ).toMatchObject({ ok: false, field: 'name' })
    expect(
      toNamedProviderConfig({ ...office, name: 'openai', max_tokens: '10' }, [])
    ).toMatchObject({ ok: false, field: 'name' })
    expect(
      toNamedProviderConfig({ ...office, max_tokens: '10' }, [office])
    ).toMatchObject({ ok: false, field: 'name' })
  })

  it('rejects URL credentials and invalid token caps before engine save', () => {
    expect(
      toNamedProviderConfig(
        { ...office, base_url: 'https://user:secret@example.com/v1', max_tokens: '10' },
        []
      )
    ).toMatchObject({ ok: false, field: 'base_url' })
    expect(toNamedProviderConfig({ ...office, max_tokens: '0' }, [])).toMatchObject({
      ok: false,
      field: 'max_tokens'
    })
  })
})

describe('provider list edits', () => {
  it('updates without mutating the input list', () => {
    const original = [office]
    const changed = { ...office, default_model: 'corp-large' }
    expect(upsertNamedProvider(original, changed, 'office-gateway')).toEqual([changed])
    expect(original).toEqual([office])
  })

  it('removes by stable name without mutating the input list', () => {
    const original = [office]
    expect(removeNamedProvider(original, 'office-gateway')).toEqual([])
    expect(original).toEqual([office])
  })
})

describe('normalizeNamedProviderKey', () => {
  it('uses the same trimmed value for testing and storage', () => {
    expect(normalizeNamedProviderKey('  sk-office  ')).toBe('sk-office')
    expect(normalizeNamedProviderKey('   ')).toBeUndefined()
  })
})
