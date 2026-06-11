import { spawn } from 'node:child_process'
import { createHash, randomBytes } from 'node:crypto'
import { existsSync } from 'node:fs'
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { createInterface } from 'node:readline'

import { test as base, expect } from '@playwright/test'
import { _electron } from 'playwright'
import type { ChildProcess } from 'node:child_process'
import type { ElectronApplication, Page } from 'playwright'

/**
 * Playwright harness (task 7.1, design decision 11): spawns the mock engine
 * as a real child process, writes `engine-state.json` + `engine.json` into a
 * temp PODCAST_READER_DATA_DIR, and launches the built app — which then
 * ADOPTS the mock through its production discovery path (PID liveness,
 * fingerprint match, authed health). No test-only branches in main.
 */

export const APP_DIR = resolve(__dirname, '..', '..')
const MOCK_PATH = join(APP_DIR, 'tests', 'mock-engine', 'server.ts')
const MAIN_ENTRY = join(APP_DIR, 'out', 'main', 'index.js')

export interface MockLogEntry {
  seq: number
  kind: string
  detail: string
}

export class MockEngine {
  constructor(
    readonly port: number,
    readonly pid: number,
    readonly token: string,
    readonly logFile: string,
    private readonly child: ChildProcess
  ) {}

  get baseUrl(): string {
    return `http://127.0.0.1:${this.port}`
  }

  /** Authenticated request against the mock's /v1 surface (test-side client). */
  async engine(path: string, init: RequestInit = {}): Promise<Response> {
    return fetch(`${this.baseUrl}${path}`, {
      ...init,
      headers: {
        authorization: `Bearer ${this.token}`,
        'content-type': 'application/json',
        ...(init.headers ?? {})
      }
    })
  }

  /**
   * Unauthenticated control-surface call (scripting/observation seam).
   * With a body (even `{}`) it POSTs; without one it GETs.
   */
  async control(path: string, body?: unknown): Promise<Response> {
    const res = await fetch(`${this.baseUrl}/__mock${path}`, {
      method: body === undefined ? 'GET' : 'POST',
      headers: { 'content-type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body)
    })
    if (!res.ok) throw new Error(`mock control ${path} failed: ${res.status}`)
    return res
  }

  /** The live ordered event log (requests, events-open/close, shutdown). */
  async log(): Promise<MockLogEntry[]> {
    const res = await this.control('/log')
    return (await res.json()) as MockLogEntry[]
  }

  /** The persisted log — readable after the mock exited (quit-order asserts). */
  async logFromFile(): Promise<MockLogEntry[]> {
    const text = await readFile(this.logFile, 'utf8')
    return text
      .split('\n')
      .filter((line) => line !== '')
      .map((line) => JSON.parse(line) as MockLogEntry)
  }

  kill(): void {
    try {
      this.child.kill('SIGKILL')
    } catch {
      // already gone
    }
  }
}

export function tokenFingerprint(token: string): string {
  return createHash('sha256').update(token, 'utf8').digest('hex').slice(0, 16)
}

export async function startMockEngine(dataDir: string, token: string): Promise<MockEngine> {
  const logFile = join(dataDir, `mock-log-${Date.now()}-${randomBytes(4).toString('hex')}.jsonl`)
  const child = spawn(process.execPath, [MOCK_PATH], {
    env: { ...process.env, MOCK_ENGINE_TOKEN: token, MOCK_LOG_FILE: logFile },
    stdio: ['ignore', 'pipe', 'pipe']
  })
  const port = await new Promise<number>((resolvePort, reject) => {
    const timer = setTimeout(() => reject(new Error('mock engine never signaled ready')), 10_000)
    if (child.stdout === null) {
      reject(new Error('mock engine has no stdout'))
      return
    }
    const rl = createInterface({ input: child.stdout })
    rl.on('line', (line) => {
      const match = /^MOCK_ENGINE_READY (.*)$/.exec(line)
      if (match !== null && match[1] !== undefined) {
        clearTimeout(timer)
        resolvePort((JSON.parse(match[1]) as { port: number }).port)
      }
    })
    child.once('exit', (code) => {
      clearTimeout(timer)
      reject(new Error(`mock engine exited early (code ${String(code)})`))
    })
    child.stderr?.on('data', (chunk: Buffer) => process.stderr.write(chunk))
  })
  if (child.pid === undefined) throw new Error('mock engine has no pid')
  return new MockEngine(port, child.pid, token, logFile, child)
}

/** Write the Phase 1 discovery handshake files the app adopts through. */
export async function writeDiscoveryFiles(dataDir: string, mock: MockEngine): Promise<void> {
  await writeFile(
    join(dataDir, 'engine-state.json'),
    JSON.stringify({ port: mock.port, token: mock.token }),
    { mode: 0o600 }
  )
  await writeFile(
    join(dataDir, 'engine.json'),
    JSON.stringify({
      port: mock.port,
      pid: mock.pid,
      token_fingerprint: tokenFingerprint(mock.token),
      version: '0.1.0'
    }),
    { mode: 0o600 }
  )
}

export interface LaunchOptions {
  dataDir: string
  userDataDir: string
  extraEnv?: Record<string, string>
  /** Env vars to strip (e.g. PODCAST_READER_ENGINE_CMD for the dev-fallback drill). */
  dropEnv?: string[]
}

export async function launchApp(opts: LaunchOptions): Promise<ElectronApplication> {
  if (!existsSync(MAIN_ENTRY)) {
    throw new Error(`app not built (missing ${MAIN_ENTRY}) — run \`npm run build\` first`)
  }
  const env: Record<string, string> = {
    ...(process.env as Record<string, string>),
    PODCAST_READER_DATA_DIR: opts.dataDir,
    PODCAST_READER_USER_DATA_DIR: opts.userDataDir,
    ...opts.extraEnv
  }
  // The dev fallback must never fire by accident in mock-engine tests; the
  // integration spec opts back in deliberately.
  delete env.ELECTRON_RENDERER_URL
  delete env.ELECTRON_RUN_AS_NODE
  for (const name of opts.dropEnv ?? []) delete env[name]
  return _electron.launch({
    args: [APP_DIR, '--no-sandbox'],
    env,
    timeout: 60_000
  })
}

/** Close every BrowserWindow (the user-quit path: window close, NOT a signal). */
export async function closeAllWindows(app: ElectronApplication): Promise<void> {
  await app.evaluate(({ BrowserWindow }) => {
    for (const window of BrowserWindow.getAllWindows()) window.close()
  })
}

/** Resolve when the Electron process has fully exited. */
export function appExited(app: ElectronApplication, timeoutMs = 30_000): Promise<void> {
  return new Promise<void>((resolveExit, reject) => {
    const timer = setTimeout(
      () => reject(new Error(`app did not exit within ${timeoutMs}ms`)),
      timeoutMs
    )
    app.process().once('exit', () => {
      clearTimeout(timer)
      resolveExit()
    })
  })
}

export interface Harness {
  dataDir: string
  userDataDir: string
  mock: MockEngine
  app: ElectronApplication
  window: Page
  /**
   * Quit-then-launch a fresh app session against a NEW mock (the previous
   * one exits with the app's shutdown POST), reusing data + userData dirs —
   * the restart drill for vault push-at-start and journal persistence.
   */
  relaunch(): Promise<void>
}

async function waitForWindow(app: ElectronApplication): Promise<Page> {
  const window = await app.firstWindow()
  await window.waitForLoadState('domcontentloaded')
  return window
}

export const test = base.extend<{ harness: Harness }>({
  harness: async ({}, use) => {
    const dataDir = await mkdtemp(join(tmpdir(), 'pr-e2e-data-'))
    const userDataDir = await mkdtemp(join(tmpdir(), 'pr-e2e-user-'))
    const token = randomBytes(24).toString('base64url')
    let mock = await startMockEngine(dataDir, token)
    await writeDiscoveryFiles(dataDir, mock)
    let app = await launchApp({ dataDir, userDataDir })
    let window = await waitForWindow(app)

    const harness: Harness = {
      dataDir,
      userDataDir,
      get mock() {
        return mock
      },
      get app() {
        return app
      },
      get window() {
        return window
      },
      relaunch: async () => {
        const exited = appExited(app)
        await closeAllWindows(app)
        await exited
        mock.kill() // normally already dead via the app's shutdown POST
        mock = await startMockEngine(dataDir, token)
        await writeDiscoveryFiles(dataDir, mock)
        app = await launchApp({ dataDir, userDataDir })
        window = await waitForWindow(app)
      }
    }

    await use(harness)

    try {
      await app.close()
    } catch {
      // already exited (quit-sequence tests)
    }
    mock.kill()
    await rm(dataDir, { recursive: true, force: true })
    await rm(userDataDir, { recursive: true, force: true })
  }
})

export { expect }

/** Wait until the engine pill reports the given supervision state. */
export async function expectEngineState(window: Page, state: string): Promise<void> {
  await expect(window.locator('.engine-pill')).toHaveAttribute('data-state', state, {
    timeout: 30_000
  })
}
