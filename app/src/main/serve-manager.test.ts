import { describe, expect, it, vi } from 'vitest'

import { SERVE_GENERATION } from './serve-journal'
import { ServeManager } from './serve-manager'
import type { GuardianEvent, GuardianProcess, ServeManagerDeps } from './serve-manager'

class MemoryJournal {
  value: ReturnType<ServeManagerDeps['journal']['read']> = { kind: 'absent' }
  readonly writes: string[] = []

  read(): ReturnType<ServeManagerDeps['journal']['read']> {
    return this.value
  }
  write(record: Parameters<ServeManagerDeps['journal']['write']>[0]): void {
    this.writes.push(record.state)
    this.value = { kind: 'record', record }
  }
  remove(): void {
    this.writes.push('removed')
    this.value = { kind: 'absent' }
  }
}

class FakeGuardian implements GuardianProcess {
  readonly actions: string[] = []
  readonly timeouts: Array<number | null | undefined> = []
  private pendingTerminal: ((event: GuardianEvent) => void) | null = null
  private readyDelivered = false
  constructor(
    private readonly events: GuardianEvent[],
    private readonly stallOnClose = false
  ) {}
  async nextEvent(_timeoutMs?: number | null): Promise<GuardianEvent> {
    this.timeouts.push(_timeoutMs)
    const event = this.events[0]
    if (event === undefined) throw new Error('no scripted guardian event')
    if (
      (event.event === 'stopped' || event.event === 'conflict' || event.event === 'error') &&
      this.readyDelivered &&
      !this.actions.includes('close')
    ) {
      return new Promise((resolve) => {
        this.pendingTerminal = resolve
      })
    }
    const delivered = this.events.shift() as GuardianEvent
    if (delivered.event === 'ready') this.readyDelivered = true
    return delivered
  }
  sendGo(): void {
    this.actions.push('go')
  }
  closeLease(): void {
    this.actions.push('close')
    if (!this.stallOnClose) this.emitTerminal()
  }
  kill(): void {
    this.actions.push('kill')
  }
  emitTerminal(): void {
    const resolve = this.pendingTerminal
    if (resolve === null) return
    const event = this.events.shift()
    this.pendingTerminal = null
    if (event !== undefined) resolve(event)
  }
}

function mapping(target = 'http://127.0.0.1:43127'): string {
  return JSON.stringify({
    TCP: { '443': { HTTPS: true } },
    Web: {
      'desktop.example.ts.net:443': { Handlers: { '/': { Proxy: target } } }
    },
    AllowFunnel: {}
  })
}

function deps(statuses: string[], guardian?: FakeGuardian): ServeManagerDeps & {
  journal: MemoryJournal
  mutations: string[]
} {
  const journal = new MemoryJournal()
  const mutations: string[] = []
  return {
    journal,
    mutations,
    readStatus: async () => statuses.shift() ?? '{}',
    disableListener: async () => {
      mutations.push('disable')
    },
    spawnGuardian: () => guardian ?? new FakeGuardian([])
  }
}

describe('ServeManager reconciliation', () => {
  it.each(['{', '[]', '{"FutureConfig":{}}'])('does not mutate on ambiguous status %s', async (status) => {
    const d = deps([status])
    const result = await new ServeManager(d).reconcile()
    expect(result.state).toBe('conflict')
    expect(d.mutations).toEqual([])
  })

  it('removes an exact journal-owned stale mapping and clears only after absence', async () => {
    const d = deps([mapping(), '{}'])
    d.journal.value = {
      kind: 'record',
      record: {
        state: 'active',
        generation: SERVE_GENERATION,
        listener: 'https:443',
        target: 'http://127.0.0.1:43127'
      }
    }
    expect(await new ServeManager(d).reconcile()).toEqual({ state: 'idle' })
    expect(d.mutations).toEqual(['disable'])
    expect(d.journal.writes).toEqual(['removed'])
  })

  it('preserves both journal and mapping when the target changed', async () => {
    const d = deps([mapping('http://127.0.0.1:9999')])
    d.journal.value = {
      kind: 'record',
      record: {
        state: 'pending',
        generation: SERVE_GENERATION,
        listener: 'https:443',
        target: 'http://127.0.0.1:43127'
      }
    }
    expect((await new ServeManager(d).reconcile()).state).toBe('conflict')
    expect(d.mutations).toEqual([])
    expect(d.journal.value.kind).toBe('record')
  })

  it('clears a stale journal when status proves the listener absent', async () => {
    const d = deps(['{}'])
    d.journal.value = {
      kind: 'record',
      record: {
        state: 'pending',
        generation: SERVE_GENERATION,
        listener: 'https:443',
        target: 'http://127.0.0.1:43127'
      }
    }
    expect(await new ServeManager(d).reconcile()).toEqual({ state: 'idle' })
    expect(d.journal.writes).toEqual(['removed'])
  })
})

describe('ServeManager guardian lease', () => {
  it('reconciles retained ownership before reporting idle without an in-memory guardian', async () => {
    const d = deps([mapping(), '{}'])
    d.journal.write({
      state: 'active',
      generation: SERVE_GENERATION,
      listener: 'https:443',
      target: 'http://127.0.0.1:43127'
    })
    expect(await new ServeManager(d).stop()).toEqual({ state: 'idle' })
    expect(d.mutations).toEqual(['disable'])
    expect(d.journal.value).toEqual({ kind: 'absent' })
  })

  it('creates no ownership record when the guardian reports a pre-bind collision', async () => {
    const guardian = new FakeGuardian([{ event: 'conflict', message: 'HTTPS 443 is occupied' }])
    const d = deps([], guardian)
    expect((await new ServeManager(d).start(8000)).state).toBe('conflict')
    expect(d.journal.value).toEqual({ kind: 'absent' })
    expect(d.journal.writes).toEqual([])
  })

  it('removes its pending journal when the final precondition detects an external mapping', async () => {
    const guardian = new FakeGuardian([
      { event: 'bound', target: 'http://127.0.0.1:43127' },
      { event: 'unowned', severity: 'conflict', message: 'HTTPS 443 became occupied' }
    ])
    const d = deps([], guardian)
    expect((await new ServeManager(d).start(8000)).state).toBe('conflict')
    expect(d.journal.writes).toEqual(['pending', 'removed'])
    expect(d.mutations).toEqual([])
  })

  it('fsyncs pending before GO and promotes only after exact ready', async () => {
    const guardian = new FakeGuardian([
      { event: 'bound', target: 'http://127.0.0.1:43127' },
      {
        event: 'ready',
        target: 'http://127.0.0.1:43127',
        url: 'https://desktop.example.ts.net/web/'
      }
    ])
    const d = deps([], guardian)
    const manager = new ServeManager(d)
    expect(await manager.start(8000)).toEqual({
      state: 'ready',
      url: 'https://desktop.example.ts.net/web/'
    })
    expect(d.journal.writes).toEqual(['pending', 'active'])
    expect(guardian.actions).toEqual(['go'])
  })

  it('closes the lease and retains ownership proof when cleanup is not proven', async () => {
    const guardian = new FakeGuardian([
      { event: 'bound', target: 'http://127.0.0.1:43127' },
      {
        event: 'ready',
        target: 'http://127.0.0.1:43127',
        url: 'https://desktop.example.ts.net/web/'
      },
      { event: 'conflict', message: 'changed' }
    ])
    const d = deps([], guardian)
    const manager = new ServeManager(d)
    await manager.start(8000)
    expect((await manager.stop()).state).toBe('conflict')
    expect(guardian.actions).toEqual(['go', 'close'])
    expect(d.journal.value.kind).toBe('record')
  })

  it('revokes the lease and clears pending after promotion failure cleanup is proven', async () => {
    const guardian = new FakeGuardian([
      { event: 'bound', target: 'http://127.0.0.1:43127' },
      {
        event: 'ready',
        target: 'http://127.0.0.1:43127',
        url: 'https://desktop.example.ts.net/web/'
      },
      { event: 'stopped' }
    ])
    const d = deps([], guardian)
    const originalWrite = d.journal.write.bind(d.journal)
    d.journal.write = (record) => {
      if (record.state === 'active') throw new Error('fsync failed')
      originalWrite(record)
    }
    expect((await new ServeManager(d).start(8000)).state).toBe('error')
    expect(guardian.actions).toEqual(['go', 'close'])
    expect(d.journal.value).toEqual({ kind: 'absent' })
  })

  it('removes active ownership only after guardian stopped and status is empty', async () => {
    const guardian = new FakeGuardian([
      { event: 'bound', target: 'http://127.0.0.1:43127' },
      {
        event: 'ready',
        target: 'http://127.0.0.1:43127',
        url: 'https://desktop.example.ts.net/web/'
      },
      { event: 'stopped' }
    ])
    const d = deps(['{}'], guardian)
    const manager = new ServeManager(d)
    await manager.start(8000)
    expect(await manager.stop()).toEqual({ state: 'idle' })
    expect(d.journal.writes).toEqual(['pending', 'active', 'removed'])
  })

  it('retains the gate holder on stop timeout and clears ownership after later cleanup', async () => {
    const guardian = new FakeGuardian(
      [
        { event: 'bound', target: 'http://127.0.0.1:43127' },
        {
          event: 'ready',
          target: 'http://127.0.0.1:43127',
          url: 'https://desktop.example.ts.net/web/'
        },
        { event: 'stopped' }
      ],
      true
    )
    const d = { ...deps(['{}'], guardian), stopTimeoutMs: 1 }
    const manager = new ServeManager(d)
    const terminal = new Promise<unknown>((resolve) => manager.setTerminalHandler(resolve))
    await manager.start(8000)
    expect((await manager.stop()).state).toBe('error')
    expect(guardian.actions).toEqual(['go', 'close'])
    expect(d.journal.value).toMatchObject({ kind: 'record', record: { state: 'active' } })
    guardian.emitTerminal()
    await expect(terminal).resolves.toEqual({ state: 'idle' })
    expect(d.journal.value).toEqual({ kind: 'absent' })
  })

  it('reports an unexpected guardian stop and reconciles the active journal', async () => {
    const guardian = new FakeGuardian([
      { event: 'bound', target: 'http://127.0.0.1:43127' },
      {
        event: 'ready',
        target: 'http://127.0.0.1:43127',
        url: 'https://desktop.example.ts.net/web/'
      },
      { event: 'stopped' }
    ])
    const d = deps(['{}'], guardian)
    const manager = new ServeManager(d)
    const terminal = new Promise<unknown>((resolve) => manager.setTerminalHandler(resolve))
    await manager.start(8000)
    expect(guardian.timeouts).toEqual([undefined, undefined, null])
    guardian.emitTerminal()
    await expect(terminal).resolves.toEqual({
      state: 'error',
      message: 'Private web access stopped unexpectedly'
    })
    expect(d.journal.writes).toEqual(['pending', 'active', 'removed'])
  })

  it('does not let an old terminal settlement remove a newer run journal', async () => {
    const oldGuardian = new FakeGuardian([
      { event: 'bound', target: 'http://127.0.0.1:43127' },
      {
        event: 'ready',
        target: 'http://127.0.0.1:43127',
        url: 'https://desktop.example.ts.net/web/'
      },
      { event: 'stopped' }
    ])
    const newGuardian = new FakeGuardian([
      { event: 'bound', target: 'http://127.0.0.1:43128' },
      {
        event: 'ready',
        target: 'http://127.0.0.1:43128',
        url: 'https://desktop.example.ts.net/web/'
      },
      { event: 'stopped' }
    ])
    let releaseStatus: ((value: string) => void) | null = null
    const d = deps([], oldGuardian)
    let spawnCount = 0
    d.spawnGuardian = () => (spawnCount++ === 0 ? oldGuardian : newGuardian)
    d.readStatus = () =>
      new Promise<string>((resolve) => {
        releaseStatus = resolve
      })
    const manager = new ServeManager(d)
    const handler = vi.fn()
    manager.setTerminalHandler(handler)
    await manager.start(8000)
    oldGuardian.emitTerminal()
    for (let attempt = 0; attempt < 10 && releaseStatus === null; attempt += 1) {
      await Promise.resolve()
    }
    expect((await manager.start(8001)).state).toBe('ready')
    if (releaseStatus === null) throw new Error('terminal status read was not started')
    const release = releaseStatus as unknown as (value: string) => void
    release('{}')
    await Promise.resolve()
    await Promise.resolve()
    expect(handler).not.toHaveBeenCalled()
    expect(d.journal.value).toMatchObject({
      kind: 'record',
      record: { state: 'active', target: 'http://127.0.0.1:43128' }
    })
  })

  it('does not publish an old terminal status-read failure over a newer run', async () => {
    const oldGuardian = new FakeGuardian([
      { event: 'bound', target: 'http://127.0.0.1:43127' },
      {
        event: 'ready',
        target: 'http://127.0.0.1:43127',
        url: 'https://desktop.example.ts.net/web/'
      },
      { event: 'stopped' }
    ])
    const newGuardian = new FakeGuardian([
      { event: 'bound', target: 'http://127.0.0.1:43128' },
      {
        event: 'ready',
        target: 'http://127.0.0.1:43128',
        url: 'https://desktop.example.ts.net/web/'
      },
      { event: 'stopped' }
    ])
    let rejectStatus: ((reason: Error) => void) | null = null
    const d = deps([], oldGuardian)
    let spawnCount = 0
    d.spawnGuardian = () => (spawnCount++ === 0 ? oldGuardian : newGuardian)
    d.readStatus = () =>
      new Promise<string>((_resolve, reject) => {
        rejectStatus = reject
      })
    const manager = new ServeManager(d)
    const handler = vi.fn()
    manager.setTerminalHandler(handler)
    await manager.start(8000)
    oldGuardian.emitTerminal()
    for (let attempt = 0; attempt < 10 && rejectStatus === null; attempt += 1) {
      await Promise.resolve()
    }
    expect((await manager.start(8001)).state).toBe('ready')
    if (rejectStatus === null) throw new Error('terminal status read was not started')
    const reject = rejectStatus as unknown as (reason: Error) => void
    reject(new Error('status command failed'))
    await Promise.resolve()
    await Promise.resolve()
    expect(handler).not.toHaveBeenCalled()
  })
})
