import { describe, expect, it, vi } from 'vitest'

import { pidIsAlive, runQuitSequence, waitForPidExit } from './quit'

interface Calls {
  order: string[]
}

function makeOps(calls: Calls, opts: { exits?: boolean; shutdownFails?: boolean } = {}) {
  return {
    abortEvents: () => {
      calls.order.push('abortEvents')
    },
    postShutdown: async () => {
      calls.order.push('postShutdown')
      if (opts.shutdownFails) throw new Error('connection refused')
    },
    waitExit: async (_timeoutMs: number) => {
      calls.order.push('waitExit')
      return opts.exits ?? true
    },
    forceKill: async () => {
      calls.order.push('forceKill')
    }
  }
}

describe('runQuitSequence', () => {
  it('aborts the SSE stream before posting shutdown (per P1)', async () => {
    const calls: Calls = { order: [] }
    const outcome = await runQuitSequence(makeOps(calls))
    expect(outcome).toBe('clean')
    expect(calls.order).toEqual(['abortEvents', 'postShutdown', 'waitExit'])
  })

  it('force-kills when the engine does not exit within the timeout', async () => {
    const calls: Calls = { order: [] }
    const outcome = await runQuitSequence(makeOps(calls, { exits: false }))
    expect(outcome).toBe('forced')
    expect(calls.order).toEqual(['abortEvents', 'postShutdown', 'waitExit', 'forceKill'])
  })

  it('proceeds to wait/force-kill when the shutdown POST fails', async () => {
    const calls: Calls = { order: [] }
    const outcome = await runQuitSequence(makeOps(calls, { shutdownFails: true, exits: false }))
    expect(outcome).toBe('forced')
    expect(calls.order).toEqual(['abortEvents', 'postShutdown', 'waitExit', 'forceKill'])
  })

  it('passes the configured timeout to waitExit', async () => {
    let seen = 0
    await runQuitSequence(
      {
        abortEvents: () => {},
        postShutdown: async () => {},
        waitExit: async (t) => {
          seen = t
          return true
        },
        forceKill: () => {}
      },
      { timeoutMs: 1234 }
    )
    expect(seen).toBe(1234)
  })
})

describe('waitForPidExit', () => {
  it('polls until the PID dies (per P7: adopted engines have no exit event)', async () => {
    let aliveChecks = 0
    const exited = await waitForPidExit(4242, {
      timeoutMs: 1000,
      intervalMs: 1,
      isAlive: () => {
        aliveChecks += 1
        return aliveChecks < 3
      },
      sleep: async () => {}
    })
    expect(exited).toBe(true)
    expect(aliveChecks).toBe(3)
  })

  it('returns false when the PID outlives the timeout', async () => {
    let now = 0
    const exited = await waitForPidExit(4242, {
      timeoutMs: 50,
      intervalMs: 10,
      isAlive: () => true,
      sleep: async (ms) => {
        now += ms
      },
      monotonicMs: () => now
    })
    expect(exited).toBe(false)
  })

  it('returns immediately when the PID is already dead', async () => {
    const exited = await waitForPidExit(4242, {
      timeoutMs: 1000,
      intervalMs: 1000,
      isAlive: () => false,
      sleep: async () => {
        throw new Error('should not sleep')
      }
    })
    expect(exited).toBe(true)
  })
})

describe('pidIsAlive', () => {
  it('reports our own pid as alive', () => {
    expect(pidIsAlive(process.pid)).toBe(true)
  })

  it('returns false for pid <= 0 without signaling (kill(0)/kill(-n) address process groups)', () => {
    const spy = vi.spyOn(process, 'kill').mockImplementation(() => true)
    try {
      expect(pidIsAlive(0)).toBe(false)
      expect(pidIsAlive(-1)).toBe(false)
      expect(spy).not.toHaveBeenCalled()
    } finally {
      spy.mockRestore()
    }
  })
})
