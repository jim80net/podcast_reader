import { describe, expect, it } from 'vitest'

import { privateWebActions, privateWebLabel } from './views/private-web-section'

describe('privateWebLabel', () => {
  it('states the tailnet boundary when disabled', () => {
    expect(privateWebLabel({ state: 'disabled' })).toContain('Nothing is exposed')
  })

  it('surfaces the private URL and conflict guidance', () => {
    expect(
      privateWebLabel({ state: 'ready', url: 'https://desktop.example.ts.net/web/' })
    ).toContain('https://desktop.example.ts.net/web/')
    expect(privateWebLabel({ state: 'conflict', message: 'HTTPS 443 is occupied' })).toContain(
      'HTTPS 443 is occupied'
    )
  })

  it('offers both retry and disable after a conflict or error', () => {
    expect(privateWebActions({ state: 'conflict', message: 'occupied' })).toEqual([
      'retry',
      'disable'
    ])
    expect(privateWebActions({ state: 'error', message: 'unavailable' })).toEqual([
      'retry',
      'disable'
    ])
  })
})
