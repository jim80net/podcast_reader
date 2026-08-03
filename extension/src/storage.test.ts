import { describe, expect, it } from 'vitest'

import { ExtensionStore, MAX_TRACKED_JOBS, STORAGE_SCHEMA_VERSION } from './storage'
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
    const area = makeArea()
    const store = new ExtensionStore(area)
    await expect(store.pairing()).resolves.toBeNull()
    await store.setPairing({ port: 51234, token: 'tok-1' })
    await expect(store.pairing()).resolves.toEqual({ port: 51234, token: 'tok-1' })
    expect(area.data.get('pairing')).toEqual({
      schema_version: STORAGE_SCHEMA_VERSION,
      port: 51234,
      token: 'tok-1'
    })
    await store.clearPairing()
    await expect(store.pairing()).resolves.toBeNull()
  })

  it('migrates only an exact valid legacy pairing', async () => {
    const area = makeArea()
    area.data.set('pairing', { port: 51234, token: 'legacy_token' })
    await expect(new ExtensionStore(area).pairing()).resolves.toEqual({ port: 51234, token: 'legacy_token' })
    expect(area.data.get('pairing')).toEqual({ schema_version: 1, port: 51234, token: 'legacy_token' })
  })

  it('rejects malformed and unknown-version pairings without destroying future state', async () => {
    const invalid = [
      { schema_version: 1, port: '51234', token: 'tok-1' },
      { schema_version: 1, port: 0, token: 'tok-1' },
      { schema_version: 1, port: 65536, token: 'tok-1' },
      { schema_version: 1, port: 1.5, token: 'tok-1' },
      { schema_version: 1, port: Number.NaN, token: 'tok-1' },
      { schema_version: 1, port: 51234, token: '' },
      { schema_version: 1, port: 51234, token: 7 },
      { schema_version: 1, port: 51234, token: 'has spaces' },
      { schema_version: 1, port: 51234, token: 'x'.repeat(513) },
      { schema_version: 1, port: 51234, token: 'tok-1', surprise: true },
      { port: 51234, token: 'tok-1', surprise: true }
    ]
    for (const value of invalid) {
      const area = makeArea()
      area.data.set('pairing', value)
      await expect(new ExtensionStore(area).pairing()).resolves.toBeNull()
    }

    const area = makeArea()
    const future = { schema_version: 2, port: 51234, token: 'future_token' }
    area.data.set('pairing', future)
    await expect(new ExtensionStore(area).pairing()).resolves.toBeNull()
    expect(area.data.get('pairing')).toBe(future)
  })

  it('refuses to persist invalid pairing values from an untyped caller', async () => {
    const store = new ExtensionStore(makeArea())
    await expect(store.setPairing({ port: Number.POSITIVE_INFINITY, token: 'tok-1' })).rejects.toThrow('invalid extension pairing')
    await expect(store.setPairing({ port: 51234, token: '' })).rejects.toThrow('invalid extension pairing')
  })
})

describe('ExtensionStore tracked jobs', () => {
  it('prepends most-recent-first and replaces same-id entries', async () => {
    const area = makeArea()
    const store = new ExtensionStore(area)
    await store.trackJob(job('a'))
    await store.trackJob(job('b'))
    await store.trackJob(job('a', { notified: true }))
    const jobs = await store.trackedJobs()
    expect(jobs.map((j) => j.id)).toEqual(['a', 'b'])
    expect(jobs[0]?.notified).toBe(true)
    expect(area.data.get('trackedJobs')).toEqual({ schema_version: 1, jobs })
  })

  it('bounds the list at MAX_TRACKED_JOBS', async () => {
    const store = new ExtensionStore(makeArea())
    for (let i = 0; i < MAX_TRACKED_JOBS + 5; i += 1) await store.trackJob(job(`j${i}`))
    const jobs = await store.trackedJobs()
    expect(jobs).toHaveLength(MAX_TRACKED_JOBS)
    expect(jobs[0]?.id).toBe(`j${MAX_TRACKED_JOBS + 4}`) // newest first

    const direct = Array.from({ length: MAX_TRACKED_JOBS + 5 }, (_, index) => job(`direct-${String(index)}`))
    await store.setTrackedJobs(direct)
    await expect(store.trackedJobs()).resolves.toEqual(direct.slice(0, MAX_TRACKED_JOBS))
  })

  it('untracks by id', async () => {
    const store = new ExtensionStore(makeArea())
    await store.trackJob(job('a'))
    await store.trackJob(job('b'))
    await store.untrackJob('a')
    await expect(store.trackedJobs()).resolves.toEqual([job('b')])
  })

  it('migrates, validates, de-duplicates, and bounds legacy tracked jobs item by item', async () => {
    const area = makeArea()
    const values: unknown[] = [
      job('valid'),
      job('valid', { notified: true }),
      { ...job('extra'), surprise: true },
      job('', {}),
      job('x'.repeat(129)),
      job('bad-source', { source: '' }),
      job('long-source', { source: 'x'.repeat(8193) }),
      job('typed-title', { title: 7 as unknown as string }),
      job('bad-title', { title: 'x'.repeat(4097) }),
      job('bad-time', { submitted_at: Number.NaN }),
      job('infinite-time', { submitted_at: Number.POSITIVE_INFINITY }),
      job('negative-time', { submitted_at: -1 }),
      job('bad-notified', { notified: 'yes' as unknown as boolean }),
      null,
      ...Array.from({ length: MAX_TRACKED_JOBS + 5 }, (_, index) => job(`bounded-${String(index)}`))
    ]
    area.data.set('trackedJobs', values)
    const jobs = await new ExtensionStore(area).trackedJobs()
    expect(jobs).toHaveLength(MAX_TRACKED_JOBS)
    expect(jobs[0]).toEqual(job('valid'))
    expect(new Set(jobs.map((item) => item.id)).size).toBe(MAX_TRACKED_JOBS)
    expect(area.data.get('trackedJobs')).toEqual({ schema_version: 1, jobs })
  })

  it('sanitizes a current envelope but preserves an unknown future version', async () => {
    const area = makeArea()
    area.data.set('trackedJobs', {
      schema_version: 1,
      jobs: [job('good'), { ...job('bad'), source: '' }]
    })
    await expect(new ExtensionStore(area).trackedJobs()).resolves.toEqual([job('good')])
    expect(area.data.get('trackedJobs')).toEqual({ schema_version: 1, jobs: [job('good')] })

    const extra = { schema_version: 1, jobs: [job('extra-envelope')], surprise: true }
    area.data.set('trackedJobs', extra)
    await expect(new ExtensionStore(area).trackedJobs()).resolves.toEqual([])
    expect(area.data.get('trackedJobs')).toBe(extra)

    const future = { schema_version: 2, jobs: [job('future')] }
    area.data.set('trackedJobs', future)
    await expect(new ExtensionStore(area).trackedJobs()).resolves.toEqual([])
    expect(area.data.get('trackedJobs')).toBe(future)
  })

  it('refuses invalid jobs at every write boundary', async () => {
    const store = new ExtensionStore(makeArea())
    await expect(store.trackJob(job('bad', { submitted_at: Number.POSITIVE_INFINITY }))).rejects.toThrow('invalid tracked job')
    await expect(store.setTrackedJobs([job('good'), job('bad', { source: '' })])).rejects.toThrow('invalid tracked jobs')
  })
})
