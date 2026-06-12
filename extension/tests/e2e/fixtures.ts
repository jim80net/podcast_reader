import { spawn } from 'node:child_process'
import { randomBytes } from 'node:crypto'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { createInterface } from 'node:readline'
import { fileURLToPath } from 'node:url'

import { chromium, expect, test as base } from '@playwright/test'
import type { BrowserContext, Page } from '@playwright/test'
import type { ChildProcess } from 'node:child_process'

import type { Pairing, TrackedJob } from '../../src/storage'

/**
 * Extension e2e harness (task 8.1, design decision 12): spawns the app's
 * mock engine (`app/tests/mock-engine/server.ts` — the one fake both TS
 * consumers share) as a separate process, then launches a persistent
 * Chromium context with the REAL built extension (`dist/`) loaded via
 * `--load-extension`. No PODCAST_READER_DATA_DIR is involved: the
 * extension's only engine inputs are a port and a token, which tests
 * script directly (seeded pairing codes via `/__mock/seed`, or a
 * pre-paired `chrome.storage.local` written through an extension page).
 *
 * The popup is driven as a tab (`chrome-extension://<id>/popup.html`) —
 * Chrome offers no automatable path to the real toolbar popup window, and
 * the page is byte-identical either way.
 */

const EXT_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..')
const DIST_DIR = join(EXT_ROOT, 'dist')
const MOCK_PATH = resolve(EXT_ROOT, '..', 'app', 'tests', 'mock-engine', 'server.ts')

// ---- mock engine (lean mirror of app/tests/e2e/fixtures.ts) --------------------

export interface MockLogEntry {
  seq: number
  kind: string
  detail: string
}

export class MockEngine {
  constructor(
    readonly port: number,
    readonly token: string,
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

  /** The live ordered event log (requests, events-open/close, pair-mint…). */
  async log(): Promise<MockLogEntry[]> {
    const res = await this.control('/log')
    return (await res.json()) as MockLogEntry[]
  }

  kill(): void {
    try {
      this.child.kill('SIGKILL')
    } catch {
      // already gone
    }
  }
}

export async function startMockEngine(token: string): Promise<MockEngine> {
  const child = spawn(process.execPath, [MOCK_PATH], {
    env: { ...process.env, MOCK_ENGINE_TOKEN: token },
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
  return new MockEngine(port, token, child)
}

// ---- harness ---------------------------------------------------------------------

export interface Harness {
  mock: MockEngine
  context: BrowserContext
  extensionId: string
  /** Open `popup.html` as a tab in the persistent context. */
  openPopup(): Promise<Page>
  /**
   * Write a verified-shaped pairing (and optionally tracked jobs) straight
   * into `chrome.storage.local` through an extension page — the fast path
   * for every non-pairing test — then reload so the popup re-inits from it.
   */
  seedStorage(page: Page, pairing: Pairing, trackedJobs?: TrackedJob[]): Promise<void>
}

export const test = base.extend<{ harness: Harness }>({
  harness: async ({}, use) => {
    const userDataDir = await mkdtemp(join(tmpdir(), 'pr-ext-e2e-'))
    const token = randomBytes(24).toString('base64url')
    const mock = await startMockEngine(token)
    // Extensions need a headed Chromium (xvfb-run -a on headless hosts).
    const context = await chromium.launchPersistentContext(userDataDir, {
      headless: false,
      args: [
        `--disable-extensions-except=${DIST_DIR}`,
        `--load-extension=${DIST_DIR}`,
        '--no-sandbox'
      ]
    })
    // The MV3 service worker is the extension-id discovery path
    // (https://playwright.dev/docs/chrome-extensions).
    const worker =
      context.serviceWorkers()[0] ?? (await context.waitForEvent('serviceworker'))
    const extensionId = new URL(worker.url()).host

    const harness: Harness = {
      mock,
      context,
      extensionId,
      openPopup: async () => {
        const page = await context.newPage()
        await page.goto(`chrome-extension://${extensionId}/popup.html`)
        return page
      },
      seedStorage: async (page, pairing, trackedJobs) => {
        const items: Record<string, unknown> =
          trackedJobs === undefined ? { pairing } : { pairing, trackedJobs }
        await page.evaluate(async (values) => {
          await chrome.storage.local.set(values)
        }, items)
        await page.reload()
      }
    }

    await use(harness)

    await context.close()
    mock.kill()
    await rm(userDataDir, { recursive: true, force: true })
  }
})

export { expect }

/** The stored pairing, read back through an extension page. */
export async function storedPairing(page: Page): Promise<Pairing | null> {
  return page.evaluate(async () => {
    const items = await chrome.storage.local.get(['pairing'])
    return (items['pairing'] as { port: number; token: string } | undefined) ?? null
  })
}

/** The tracked-job list, read back through an extension page. */
export async function storedTrackedJobs(page: Page): Promise<TrackedJob[]> {
  return page.evaluate(async () => {
    const items = await chrome.storage.local.get(['trackedJobs'])
    return (items['trackedJobs'] as TrackedJob[] | undefined) ?? []
  })
}
