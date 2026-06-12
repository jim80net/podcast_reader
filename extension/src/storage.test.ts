import { describe, expect, it } from 'vitest'

import { ExtensionStore, MAX_TRACKED_JOBS } from './storage'
import type { KeyValueArea, TrackedJob } from './storage'

function makeArea(): KeyValueArea & { data: Map<string, unknown> } {
  const data = new Map<string, unknown>()
  return {
    data,
    get(keys) {
      const out: Record<string, unknown> = {}
      for (const key of keys) if (data.has(key)) out[key] = data.get(key)
      return Promise.resolve(out)
    },
    set(items) {
      for (const [key, value] of Object.entries(items)) data.set(key, value)
      return Promise.resolve()
    },
    remove(keys) {
      for (const key of Array.isArray(keys) ? keys : [keys]) data.delete(key)
      return Promise.resolve()
    }
  }
}

function job(id: string, overrides: Partial<TrackedJob> = {}): TrackedJob {
  return { id, source: `https://e.com/${id}`, title: null, submitted_at: 1, notified: false, ...overrides }
}

describe('ExtensionStore pairing', () => {
  it('round-trips a pairing and clears it', async () => {
    const store = new ExtensionStore(makeArea())
    await expect(store.pairing()).resolves.toBeNull()
    await store.setPairing({ port: 51234, token: 'tok-1' })
    await expect(store.pairing()).resolves.toEqual({ port: 51234, token: 'tok-1' })
    await store.clearPairing()
    await expect(store.pairing()).resolves.toBeNull()
  })

  it('rejects malformed stored values instead of returning them', async () => {
    const area = makeArea()
    area.data.set('pairing', { port: 'not-a-number', token: 7 })
    await expect(new ExtensionStore(area).pairing()).resolves.toBeNull()
  })
})

describe('ExtensionStore tracked jobs', () => {
  it('prepends most-recent-first and replaces same-id entries', async () => {
    const store = new ExtensionStore(makeArea())
    await store.trackJob(job('a'))
    await store.trackJob(job('b'))
    await store.trackJob(job('a', { notified: true }))
    const jobs = await store.trackedJobs()
    expect(jobs.map((j) => j.id)).toEqual(['a', 'b'])
    expect(jobs[0]?.notified).toBe(true)
  })

  it('bounds the list at MAX_TRACKED_JOBS', async () => {
    const store = new ExtensionStore(makeArea())
    for (let i = 0; i < MAX_TRACKED_JOBS + 5; i += 1) await store.trackJob(job(`j${i}`))
    const jobs = await store.trackedJobs()
    expect(jobs).toHaveLength(MAX_TRACKED_JOBS)
    expect(jobs[0]?.id).toBe(`j${MAX_TRACKED_JOBS + 4}`) // newest first
  })

  it('untracks by id', async () => {
    const store = new ExtensionStore(makeArea())
    await store.trackJob(job('a'))
    await store.trackJob(job('b'))
    await store.untrackJob('a')
    await expect(store.trackedJobs()).resolves.toEqual([job('b')])
  })
})
