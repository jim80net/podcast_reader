import { describe, expect, it } from 'vitest'

import { EngineRequestError } from './client'
import { probeConnection } from './connection'

const pairing = { port: 51234, token: 'tok-1' }

describe('probeConnection', () => {
  it('reports unpaired when nothing is stored', async () => {
    await expect(probeConnection(null, () => Promise.resolve({}))).resolves.toEqual({
      state: 'unpaired'
    })
  })

  it('reports connected when the authed health probe succeeds', async () => {
    await expect(probeConnection(pairing, () => Promise.resolve({}))).resolves.toEqual({
      state: 'connected',
      pairing
    })
  })

  it('maps a 401 to the re-pair flow (token rotated), keeping the pairing visible', async () => {
    await expect(
      probeConnection(pairing, () => Promise.reject(new EngineRequestError(401, 'unauthorized')))
    ).resolves.toEqual({ state: 'unauthorized', pairing })
  })

  it('maps a connection failure to engine-down with the pairing kept', async () => {
    await expect(
      probeConnection(pairing, () => Promise.reject(new TypeError('fetch failed')))
    ).resolves.toEqual({ state: 'engine-down', pairing })
  })

  it('treats non-401 HTTP failures as engine trouble, not a pairing loss', async () => {
    await expect(
      probeConnection(pairing, () => Promise.reject(new EngineRequestError(500, 'boom')))
    ).resolves.toEqual({ state: 'engine-down', pairing })
  })
})
