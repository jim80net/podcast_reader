import { execFile, spawn } from 'node:child_process'
import { createInterface } from 'node:readline'
import { promisify } from 'node:util'

import { resolveGuardianCommand } from './engine-cmd'
import type { GuardianEvent, GuardianProcess, ServeManagerDeps } from './serve-manager'
import type { ServeJournalLike } from './serve-manager'

const execFileAsync = promisify(execFile)
const MAX_EVENT_LINE = 4096
const EVENT_TIMEOUT_MS = 15_000

function object(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

export function parseGuardianEvent(line: string): GuardianEvent | null {
  if (line.length > MAX_EVENT_LINE) return null
  let value: unknown
  try {
    value = JSON.parse(line)
  } catch {
    return null
  }
  const event = object(value)
  if (event === null || typeof event['event'] !== 'string') return null
  if (
    event['event'] === 'bound' &&
    Object.keys(event).length === 2 &&
    typeof event['target'] === 'string'
  ) {
    return { event: 'bound', target: event['target'] }
  }
  if (
    event['event'] === 'ready' &&
    Object.keys(event).length === 3 &&
    typeof event['target'] === 'string' &&
    typeof event['url'] === 'string'
  ) {
    return { event: 'ready', target: event['target'], url: event['url'] }
  }
  if (event['event'] === 'stopped' && Object.keys(event).length === 1) {
    return { event: 'stopped' }
  }
  if (
    (event['event'] === 'conflict' || event['event'] === 'error') &&
    Object.keys(event).length === 2 &&
    typeof event['message'] === 'string'
  ) {
    return { event: event['event'], message: event['message'] }
  }
  if (
    event['event'] === 'unowned' &&
    Object.keys(event).length === 3 &&
    (event['severity'] === 'error' || event['severity'] === 'conflict') &&
    typeof event['message'] === 'string'
  ) {
    return { event: 'unowned', severity: event['severity'], message: event['message'] }
  }
  return null
}

export class SpawnedGuardian implements GuardianProcess {
  private readonly queued: GuardianEvent[] = []
  private readonly waiters: Array<{
    resolve(event: GuardianEvent): void
    reject(reason: Error): void
    timer: NodeJS.Timeout | null
  }> = []
  private terminal: Error | null = null

  constructor(
    private readonly child: ReturnType<typeof spawn>,
    log: (message: string) => void
  ) {
    if (child.stdout === null) {
      this.fail(new Error('guardian stdout is unavailable'))
      return
    }
    const lines = createInterface({ input: child.stdout })
    lines.on('line', (line) => {
      const event = parseGuardianEvent(line)
      if (event === null) {
        this.fail(new Error('guardian emitted an invalid protocol event'))
        child.kill('SIGKILL')
        return
      }
      const waiter = this.waiters.shift()
      if (waiter === undefined) this.queued.push(event)
      else {
        if (waiter.timer !== null) clearTimeout(waiter.timer)
        waiter.resolve(event)
      }
    })
    child.stderr?.on('data', (chunk: Buffer | string) => {
      // Guardian stderr must never contain credentials; cap each diagnostic
      // before it reaches application logs nevertheless.
      log(`private web guardian: ${String(chunk).slice(0, 1024).trim()}`)
    })
    child.stdin?.on('error', (cause: NodeJS.ErrnoException) => {
      this.fail(new Error(`guardian lease pipe failed: ${cause.code ?? cause.message}`))
    })
    child.once('error', (cause) => this.fail(new Error(`guardian spawn failed: ${cause.message}`)))
    child.once('exit', (code, signal) => {
      this.fail(new Error(`guardian exited (${code ?? signal ?? 'unknown'})`))
    })
  }

  nextEvent(timeoutMs: number | null = EVENT_TIMEOUT_MS): Promise<GuardianEvent> {
    const queued = this.queued.shift()
    if (queued !== undefined) return Promise.resolve(queued)
    if (this.terminal !== null) return Promise.reject(this.terminal)
    return new Promise((resolve, reject) => {
      const waiter = {
        resolve,
        reject,
        timer:
          timeoutMs === null
            ? null
            : setTimeout(() => {
                const index = this.waiters.indexOf(waiter)
                if (index >= 0) this.waiters.splice(index, 1)
                reject(new Error('guardian protocol event timed out'))
              }, timeoutMs)
      }
      this.waiters.push(waiter)
    })
  }

  sendGo(): void {
    if (
      this.child.stdin === null ||
      this.child.stdin.destroyed ||
      this.child.stdin.writableEnded
    ) {
      this.fail(new Error('guardian lease pipe is unavailable'))
      return
    }
    this.child.stdin.write('GO\n', (cause) => {
      if (cause !== null && cause !== undefined) {
        this.fail(new Error(`guardian lease write failed: ${cause.message}`))
      }
    })
  }

  closeLease(): void {
    this.child.stdin?.end()
  }

  kill(): void {
    this.child.kill('SIGKILL')
  }

  private fail(reason: Error): void {
    if (this.terminal !== null) return
    this.terminal = reason
    for (const waiter of this.waiters.splice(0)) {
      if (waiter.timer !== null) clearTimeout(waiter.timer)
      waiter.reject(reason)
    }
  }
}

export function defaultServeManagerDeps(opts: {
  journal: ServeJournalLike
  env: Record<string, string | undefined>
  resourcesPath: string | null
  devCwd: string | null
  platform: NodeJS.Platform
  fileExists(path: string): boolean
  log(message: string): void
}): ServeManagerDeps {
  const tailscale = opts.platform === 'win32' ? 'tailscale.exe' : 'tailscale'
  return {
    journal: opts.journal,
    readStatus: async () => {
      const { stdout } = await execFileAsync(tailscale, ['serve', 'status', '--json'], {
        env: opts.env as NodeJS.ProcessEnv,
        timeout: 5000,
        maxBuffer: 1024 * 1024,
        windowsHide: true
      })
      return stdout
    },
    disableListener: async () => {
      await execFileAsync(tailscale, ['serve', '--yes', '--https=443', 'off'], {
        env: opts.env as NodeJS.ProcessEnv,
        timeout: 10_000,
        maxBuffer: 1024 * 1024,
        windowsHide: true
      })
    },
    spawnGuardian: (enginePort) => {
      const resolved = resolveGuardianCommand(opts)
      const [command, ...baseArgs] = resolved.argv
      if (command === undefined) throw new Error('empty guardian command')
      const child = spawn(command, [...baseArgs, '--engine-port', String(enginePort)], {
        env: opts.env as NodeJS.ProcessEnv,
        cwd: resolved.posture === 'packaged' ? undefined : (opts.devCwd ?? undefined),
        stdio: ['pipe', 'pipe', 'pipe'],
        windowsHide: true
      })
      return new SpawnedGuardian(child, opts.log)
    }
  }
}
