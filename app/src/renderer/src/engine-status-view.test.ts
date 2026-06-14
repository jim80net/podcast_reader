import { describe, expect, it } from 'vitest'

import { engineStatusView } from './engine-status-view'
import type { EngineStatus } from '../../shared/ipc'

/**
 * The pure status → view-model mapping behind the engine pill/banner. The
 * exhaustiveness guard (assertNever) lives in the same module, so a new
 * EngineStatus member that is added without an arm fails the typecheck — these
 * tests pin the runtime text/flag mapping the renderer paints.
 */

describe('engineStatusView', () => {
  it('maps starting', () => {
    expect(engineStatusView({ state: 'starting' })).toEqual({
      pill: 'engine starting…',
      banner: null,
      showRestart: false
    })
  })

  it('maps ready (with and without adoption)', () => {
    expect(
      engineStatusView({
        state: 'ready',
        port: 1,
        pid: 2,
        version: '0.3.0',
        adopted: false
      } as EngineStatus)
    ).toEqual({ pill: 'engine v0.3.0', banner: null, showRestart: false })

    expect(
      engineStatusView({
        state: 'ready',
        port: 1,
        pid: 2,
        version: '0.3.0',
        adopted: true
      } as EngineStatus)
    ).toEqual({ pill: 'engine v0.3.0 (adopted)', banner: null, showRestart: false })
  })

  it('maps restarting with the attempt counter', () => {
    expect(
      engineStatusView({ state: 'restarting', attempt: 2, maxAttempts: 3 })
    ).toEqual({
      pill: 'engine restarting…',
      banner: 'Reconnecting to engine… (attempt 2/3)',
      showRestart: false
    })
  })

  it('maps failed and offers a manual restart', () => {
    expect(engineStatusView({ state: 'failed', message: 'boom' })).toEqual({
      pill: 'engine failed',
      banner: 'Engine failed to start: boom',
      showRestart: true
    })
  })

  it('maps stopped', () => {
    expect(engineStatusView({ state: 'stopped' })).toEqual({
      pill: 'engine stopped',
      banner: null,
      showRestart: false
    })
  })
})
