import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { SERVE_GENERATION, ServeOwnershipJournal } from './serve-journal'

describe('ServeOwnershipJournal', () => {
  let dir: string
  let path: string

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), 'pr-serve-journal-'))
    path = join(dir, 'private-web-serve.json')
  })
  afterEach(() => rmSync(dir, { recursive: true, force: true }))

  it('distinguishes an absent journal from a corrupt one', () => {
    const journal = new ServeOwnershipJournal(path)
    expect(journal.read()).toEqual({ kind: 'absent' })
    writeFileSync(path, '{')
    expect(journal.read()).toEqual({ kind: 'conflict', reason: 'ownership journal is unreadable' })
  })

  it('atomically persists pending and active records without unrelated fields', () => {
    const journal = new ServeOwnershipJournal(path)
    const pending = {
      state: 'pending' as const,
      generation: SERVE_GENERATION,
      listener: 'https:443' as const,
      target: 'http://127.0.0.1:43127'
    }
    journal.write(pending)
    expect(journal.read()).toEqual({ kind: 'record', record: pending })

    const active = { ...pending, state: 'active' as const }
    journal.write(active)
    expect(journal.read()).toEqual({ kind: 'record', record: active })
    expect(JSON.parse(readFileSync(path, 'utf8'))).toEqual(active)
    expect(existsSync(`${path}.tmp`)).toBe(false)
  })

  it.each([
    {},
    { state: 'pending', generation: 'future', listener: 'https:443', target: 'http://127.0.0.1:1' },
    { state: 'active', generation: SERVE_GENERATION, listener: 'http:443', target: 'http://127.0.0.1:1' },
    { state: 'active', generation: SERVE_GENERATION, listener: 'https:443', target: 'http://localhost:1' },
    { state: 'active', generation: SERVE_GENERATION, listener: 'https:443', target: 'http://127.0.0.1:1', extra: true }
  ])('treats invalid record %j as a conflict', (record) => {
    writeFileSync(path, JSON.stringify(record))
    expect(new ServeOwnershipJournal(path).read()).toMatchObject({ kind: 'conflict' })
  })

  it('removes an ownership record durably and tolerates absence', () => {
    const journal = new ServeOwnershipJournal(path)
    journal.write({
      state: 'active',
      generation: SERVE_GENERATION,
      listener: 'https:443',
      target: 'http://127.0.0.1:43127'
    })
    journal.remove()
    journal.remove()
    expect(journal.read()).toEqual({ kind: 'absent' })
  })

  it('uses the Windows destination-flush path through write, promote, and remove', () => {
    const journal = new ServeOwnershipJournal(path, 'win32')
    journal.write({
      state: 'pending',
      generation: SERVE_GENERATION,
      listener: 'https:443',
      target: 'http://127.0.0.1:43127'
    })
    journal.write({
      state: 'active',
      generation: SERVE_GENERATION,
      listener: 'https:443',
      target: 'http://127.0.0.1:43127'
    })
    journal.remove()
    expect(journal.read()).toEqual({ kind: 'absent' })
  })
})
