import {
  closeSync,
  existsSync,
  fsyncSync,
  mkdirSync,
  openSync,
  readFileSync,
  renameSync,
  unlinkSync,
  writeFileSync
} from 'node:fs'
import { dirname } from 'node:path'

export const SERVE_GENERATION = 'tailnet-web-m1-v1' as const

export interface ServeOwnershipRecord {
  state: 'pending' | 'active'
  generation: typeof SERVE_GENERATION
  listener: 'https:443'
  target: string
}

export type ServeJournalRead =
  | { kind: 'absent' }
  | { kind: 'record'; record: ServeOwnershipRecord }
  | { kind: 'conflict'; reason: string }

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isLoopbackTarget(value: unknown): value is string {
  if (typeof value !== 'string') return false
  const match = /^http:\/\/127\.0\.0\.1:(\d{1,5})$/.exec(value)
  if (match === null) return false
  const port = Number(match[1])
  return port >= 1 && port <= 65535
}

function parseRecord(value: unknown): ServeOwnershipRecord | null {
  if (!isPlainObject(value)) return null
  const keys = Object.keys(value).sort()
  if (keys.join(',') !== 'generation,listener,state,target') return null
  if (value['state'] !== 'pending' && value['state'] !== 'active') return null
  if (value['generation'] !== SERVE_GENERATION || value['listener'] !== 'https:443') return null
  if (!isLoopbackTarget(value['target'])) return null
  return {
    state: value['state'],
    generation: SERVE_GENERATION,
    listener: 'https:443',
    target: value['target']
  }
}

function syncDirectory(path: string): void {
  const fd = openSync(path, 'r')
  try {
    fsyncSync(fd)
  } finally {
    closeSync(fd)
  }
}

function syncFile(path: string): void {
  // FlushFileBuffers requires a handle opened with write access on Windows.
  const fd = openSync(path, 'r+')
  try {
    fsyncSync(fd)
  } finally {
    closeSync(fd)
  }
}

/** Crash-consistent ownership proof for the one managed HTTPS listener. */
export class ServeOwnershipJournal {
  constructor(
    private readonly path: string,
    private readonly platform: NodeJS.Platform = process.platform
  ) {}

  read(): ServeJournalRead {
    if (!existsSync(this.path)) return { kind: 'absent' }
    try {
      const record = parseRecord(JSON.parse(readFileSync(this.path, 'utf8')))
      return record === null
        ? { kind: 'conflict', reason: 'ownership journal has an unexpected shape' }
        : { kind: 'record', record }
    } catch {
      return { kind: 'conflict', reason: 'ownership journal is unreadable' }
    }
  }

  write(record: ServeOwnershipRecord): void {
    const checked = parseRecord(record)
    if (checked === null) throw new Error('refusing to write an invalid Serve ownership record')
    const parent = dirname(this.path)
    mkdirSync(parent, { recursive: true })
    const tmp = `${this.path}.tmp`
    const fd = openSync(tmp, 'w', 0o600)
    try {
      writeFileSync(fd, JSON.stringify(checked))
      fsyncSync(fd)
    } finally {
      closeSync(fd)
    }
    renameSync(tmp, this.path)
    if (this.platform === 'win32') {
      // Node's fsync maps to FlushFileBuffers on Windows, which rejects
      // directory handles. Reopen and flush the renamed destination instead:
      // this is the strongest supported Node primitive after atomic rename.
      syncFile(this.path)
    } else {
      syncDirectory(parent)
    }
  }

  remove(): void {
    if (!existsSync(this.path)) return
    unlinkSync(this.path)
    if (this.platform !== 'win32') syncDirectory(dirname(this.path))
    // A stale deletion after a Windows crash is safe: reconciliation sees the
    // old record plus an absent mapping and repeats removal before any spawn.
  }
}
