import { describe, expect, it } from 'vitest'

import { AppStore } from './store'
import type { JobRecord, PipelineEvent } from '../../shared/types'

function job(overrides: Partial<JobRecord> = {}): JobRecord {
  return {
    id: 'j1',
    source: 'https://example.com/v',
    title: null,
    state: 'queued',
    error: null,
    events: [],
    result: null,
    created_at: 100,
    updated_at: 100,
    ...overrides
  }
}

function event(overrides: Partial<PipelineEvent> = {}): PipelineEvent {
  return {
    kind: 'step_started',
    step: 'resolve',
    message: '',
    data: { job_id: 'j1' },
    ...overrides
  }
}

describe('AppStore', () => {
  it('exposes engine and jobs as getter-only views — mutation goes through the mutators', () => {
    const proto = AppStore.prototype as object
    for (const name of ['engine', 'jobs'] as const) {
      const descriptor = Object.getOwnPropertyDescriptor(proto, name)
      expect(descriptor?.get, `${name} should be a prototype getter`).toBeTypeOf('function')
      expect(descriptor?.set, `${name} should have no public setter`).toBeUndefined()
    }
  })

  it('notifies subscribers on every mutator and reflects the change in the getters', () => {
    const store = new AppStore()
    let notifications = 0
    store.subscribe(() => {
      notifications += 1
    })

    store.setEngine({ state: 'stopped' })
    expect(store.engine).toEqual({ state: 'stopped' })
    expect(notifications).toBe(1)

    store.hydrate([job()])
    expect([...store.jobs.keys()]).toEqual(['j1'])
    expect(notifications).toBe(2)

    store.applyEvent(event())
    expect(store.jobs.get('j1')?.state).toBe('running')
    expect(notifications).toBe(3)

    store.upsert(job({ id: 'j2' }))
    expect(store.jobs.size).toBe(2)
    expect(notifications).toBe(4)

    store.remove('j2')
    expect(store.jobs.size).toBe(1)
    expect(notifications).toBe(5)
  })

  it('flags unknown-job events for re-hydration without notifying', () => {
    const store = new AppStore()
    let notifications = 0
    store.subscribe(() => {
      notifications += 1
    })
    expect(store.applyEvent(event({ data: { job_id: 'ghost' } }))).toBe(true)
    expect(notifications).toBe(0)
  })

  it('stops notifying after unsubscribe', () => {
    const store = new AppStore()
    let notifications = 0
    const unsubscribe = store.subscribe(() => {
      notifications += 1
    })
    unsubscribe()
    store.setEngine({ state: 'stopped' })
    expect(notifications).toBe(0)
  })
})
