import { describe, expect, it } from 'vitest'

import { createJobsHydrator } from './jobs-hydrator'
import type { JobRecord } from '../../shared/types'

function job(id: string): JobRecord {
  return {
    id,
    source: 'https://example.com/v',
    title: null,
    state: 'queued',
    error: null,
    events: [],
    result: null,
    overrides: null,
    models: null,
    created_at: 100,
    updated_at: 100
  }
}

interface Deferred {
  resolve: (records: JobRecord[]) => void
  reject: (err: Error) => void
}

function makeWorld(): {
  hydrator: ReturnType<typeof createJobsHydrator>
  pending: Deferred[]
  hydrations: JobRecord[][]
} {
  const pending: Deferred[] = []
  const hydrations: JobRecord[][] = []
  const hydrator = createJobsHydrator(
    () =>
      new Promise<JobRecord[]>((resolve, reject) => {
        pending.push({ resolve, reject })
      }),
    { hydrate: (records) => hydrations.push([...records]) }
  )
  return { hydrator, pending, hydrations }
}

describe('createJobsHydrator', () => {
  it('applies a single refresh', async () => {
    const world = makeWorld()
    const done = world.hydrator.refresh()
    world.pending[0]?.resolve([job('a')])
    await done
    expect(world.hydrations).toEqual([[job('a')]])
  })

  it('ignores an earlier fetch that resolves after a later one (no last-resolved-wins)', async () => {
    const world = makeWorld()
    const first = world.hydrator.refresh()
    const second = world.hydrator.refresh()
    // resolve out of order: newest first, then the stale one
    world.pending[1]?.resolve([job('new')])
    await second
    world.pending[0]?.resolve([job('stale')])
    await first
    expect(world.hydrations).toEqual([[job('new')]])
  })

  it('a hydration push supersedes any fetch still in flight', async () => {
    const world = makeWorld()
    const inFlight = world.hydrator.refresh()
    world.hydrator.applyPush([job('pushed')])
    world.pending[0]?.resolve([job('stale')])
    await inFlight
    expect(world.hydrations).toEqual([[job('pushed')]])
  })

  it('swallows fetch failures (engine not ready — the hydration push follows)', async () => {
    const world = makeWorld()
    const done = world.hydrator.refresh()
    world.pending[0]?.reject(new Error('engine starting'))
    await expect(done).resolves.toBeUndefined()
    expect(world.hydrations).toEqual([])
  })
})
