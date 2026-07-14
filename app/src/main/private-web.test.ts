import { describe, expect, it, vi } from 'vitest'

import { PrivateWebController } from './private-web'
import type { ServeTransportState } from './serve-manager'

describe('PrivateWebController', () => {
  it('reconciles a stranded ownership record even after the preference was disabled', async () => {
    const reconcile = vi.fn(async () => ({ state: 'idle' as const }))
    const controller = new PrivateWebController({
      config: { privateWebEnabled: () => false, setPrivateWebEnabled: vi.fn() },
      serve: {
        needsReconciliation: () => true,
        setTerminalHandler: vi.fn(),
        reconcile,
        start: vi.fn(),
        stop: async () => ({ state: 'idle' })
      },
      enginePort: () => null,
      send: vi.fn(),
      log: vi.fn()
    })
    await controller.beforeEngineSpawn()
    expect(reconcile).toHaveBeenCalledOnce()
    expect(controller.status).toEqual({ state: 'disabled' })
  })

  it('keeps ordinary engine startup available when reconciliation conflicts', async () => {
    let enabled = true
    const send = vi.fn()
    const controller = new PrivateWebController({
      config: {
        privateWebEnabled: () => enabled,
        setPrivateWebEnabled: (value) => {
          enabled = value
        }
      },
      serve: {
        needsReconciliation: () => false,
        setTerminalHandler: vi.fn(),
        reconcile: async () => ({ state: 'conflict', message: 'occupied' }),
        start: vi.fn(),
        stop: async () => ({ state: 'idle' })
      },
      enginePort: () => 8000,
      send,
      log: vi.fn()
    })
    await controller.beforeEngineSpawn()
    await controller.afterEngineReady(8000)
    expect(controller.status).toEqual({ state: 'conflict', message: 'occupied' })
    expect(await controller.setEnabled(false)).toEqual({ state: 'disabled' })
    expect(enabled).toBe(false)
  })

  it('explicit enable reconciles before starting against the ready engine', async () => {
    let enabled = false
    const order: string[] = []
    const controller = new PrivateWebController({
      config: {
        privateWebEnabled: () => enabled,
        setPrivateWebEnabled: (value) => {
          enabled = value
          order.push(`preference:${value}`)
        }
      },
      serve: {
        needsReconciliation: () => false,
        setTerminalHandler: vi.fn(),
        reconcile: async () => {
          order.push('reconcile')
          return { state: 'idle' }
        },
        start: async (port) => {
          order.push(`start:${port}`)
          return { state: 'ready', url: 'https://desktop.example.ts.net/web/' }
        },
        stop: async () => ({ state: 'idle' })
      },
      enginePort: () => 8000,
      send: vi.fn(),
      log: vi.fn()
    })
    expect(await controller.setEnabled(true)).toEqual({
      state: 'ready',
      url: 'https://desktop.example.ts.net/web/'
    })
    expect(order).toEqual(['preference:true', 'reconcile', 'start:8000'])
  })

  it('closes the guardian before persisting disablement', async () => {
    let enabled = true
    const order: string[] = []
    const controller = new PrivateWebController({
      config: {
        privateWebEnabled: () => enabled,
        setPrivateWebEnabled: (value) => {
          enabled = value
          order.push(`preference:${value}`)
        }
      },
      serve: {
        needsReconciliation: () => false,
        setTerminalHandler: vi.fn(),
        reconcile: async () => ({ state: 'idle' }),
        start: async () => ({ state: 'error', message: 'unused' }),
        stop: async () => {
          order.push('stop')
          return { state: 'idle' }
        }
      },
      enginePort: () => 8000,
      send: vi.fn(),
      log: vi.fn()
    })
    expect(await controller.setEnabled(false)).toEqual({ state: 'disabled' })
    expect(order).toEqual(['stop', 'preference:false'])
  })

  it('persists opt-out but does not claim disabled when cleanup is unverified', async () => {
    let enabled = true
    const controller = new PrivateWebController({
      config: {
        privateWebEnabled: () => enabled,
        setPrivateWebEnabled: (value) => {
          enabled = value
        }
      },
      serve: {
        needsReconciliation: () => true,
        setTerminalHandler: vi.fn(),
        reconcile: async () => ({ state: 'idle' }),
        start: vi.fn(),
        stop: async () => ({ state: 'conflict', message: 'cleanup could not be verified' })
      },
      enginePort: () => 8000,
      send: vi.fn(),
      log: vi.fn()
    })
    expect(await controller.setEnabled(false)).toEqual({
      state: 'conflict',
      message: 'cleanup could not be verified'
    })
    expect(enabled).toBe(false)
  })

  it('returns to disabled when delayed cleanup finishes after an opt-out', async () => {
    let enabled = true
    let terminalHandler: ((state: ServeTransportState) => void) | null = null
    const controller = new PrivateWebController({
      config: {
        privateWebEnabled: () => enabled,
        setPrivateWebEnabled: (value) => {
          enabled = value
        }
      },
      serve: {
        needsReconciliation: () => true,
        setTerminalHandler: (handler) => {
          terminalHandler = handler
        },
        reconcile: async () => ({ state: 'idle' }),
        start: vi.fn(),
        stop: async () => ({ state: 'error', message: 'cleanup still pending' })
      },
      enginePort: () => 8000,
      send: vi.fn(),
      log: vi.fn()
    })
    expect((await controller.setEnabled(false)).state).toBe('error')
    if (terminalHandler === null) throw new Error('terminal handler was not registered')
    const finish = terminalHandler as unknown as (state: ServeTransportState) => void
    finish({ state: 'idle' })
    await Promise.resolve()
    await Promise.resolve()
    expect(controller.status).toEqual({ state: 'disabled' })
  })
})
