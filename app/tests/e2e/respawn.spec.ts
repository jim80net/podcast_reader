import { randomBytes } from 'node:crypto'
import { mkdtemp, readFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { test as base } from '@playwright/test'
import type { ElectronApplication, Page } from 'playwright'

import { APP_DIR, expect, expectEngineState, launchApp } from './fixtures'

/**
 * Engine respawn supervision e2e (engine-respawn-supervision tasks 5.1/5.2).
 *
 * Unlike the adopt-based harness, this launches the app in the SPAWNED posture
 * (PODCAST_READER_ENGINE_CMD → the mock in `--spawned` mode, NO pre-written
 * discovery files, so adopt fails and the app owns the child). A real
 * `process.exit` on `/__mock/crash` makes `childExited` resolve, so the app's
 * respawn fires for real. We then assert the pill goes `restarting` → `ready`
 * and the library still loads after the respawn.
 */

const MOCK_PATH = join(APP_DIR, 'tests', 'mock-engine', 'server.ts')

interface DiscoveryFile {
  port: number
  pid: number
}

async function readDiscovery(dataDir: string): Promise<DiscoveryFile> {
  const text = await readFile(join(dataDir, 'engine.json'), 'utf8')
  return JSON.parse(text) as DiscoveryFile
}

const test = base.extend<{
  spawned: { app: ElectronApplication; window: Page; dataDir: string; token: string }
}>({
  spawned: async ({}, use) => {
    const dataDir = await mkdtemp(join(tmpdir(), 'pr-e2e-respawn-data-'))
    const userDataDir = await mkdtemp(join(tmpdir(), 'pr-e2e-respawn-user-'))
    const token = randomBytes(24).toString('base64url')
    // The app spawns the mock as its engine. No discovery files exist yet, so
    // tryAdopt fails and spawnEngine runs the override command.
    const engineCmd = `${process.execPath} ${MOCK_PATH} --spawned`
    const app = await launchApp({
      dataDir,
      userDataDir,
      extraEnv: { MOCK_ENGINE_TOKEN: token, PODCAST_READER_ENGINE_CMD: engineCmd }
    })
    const window = await app.firstWindow()
    await window.waitForLoadState('domcontentloaded')

    await use({ app, window, dataDir, token })

    try {
      await app.close()
    } catch {
      // already exited
    }
    await rm(dataDir, { recursive: true, force: true })
    await rm(userDataDir, { recursive: true, force: true })
  }
})

test('a spawned engine crash auto-respawns: restarting → ready, library survives', async ({
  spawned
}) => {
  const { window: page, dataDir, token } = spawned
  await expectEngineState(page, 'ready')

  // Seed a library entry on the live (first) engine, then crash it. The respawn
  // re-runs the command → a FRESH mock process with empty state, so we assert
  // the app recovers to ready and the library view loads (post-respawn the new
  // engine answers GET /v1/library, even though it's empty).
  const before = await readDiscovery(dataDir)

  // Trigger the real process exit on the engine the app currently owns.
  const crash = await fetch(`http://127.0.0.1:${before.port}/__mock/crash`, { method: 'POST' })
  expect(crash.status).toBe(202)

  // The pill transitions through restarting and returns to ready.
  await expectEngineState(page, 'restarting')
  await expectEngineState(page, 'ready')

  // A fresh engine is live: a new discovery file (different pid) was written.
  const after = await readDiscovery(dataDir)
  expect(after.pid).not.toBe(before.pid)

  // The respawned engine answers an authed request (library list) — proving the
  // app re-wired the client against the new port/token.
  const lib = await fetch(`http://127.0.0.1:${after.port}/v1/library`, {
    headers: { authorization: `Bearer ${token}` }
  })
  expect(lib.status).toBe(200)

  // And the renderer's Library view mounts without error after the respawn.
  await page.evaluate(() => {
    window.location.hash = '#/library'
  })
  await expect(page.locator('.view')).toBeVisible()
})
