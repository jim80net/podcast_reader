import { spawn } from 'node:child_process'
import { mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import {
  appExited,
  closeAllWindows,
  expect,
  expectEngineState,
  launchApp,
  test,
  tokenFingerprint
} from './fixtures'

/**
 * Engine supervision e2e (app-shell spec: discovery handshake, spawn
 * readiness, quit sequence): adopt through the production path, stale
 * discovery surfaced as spawn-failure messaging, and the window-close quit
 * sequence observed by the mock (per P1).
 */

test('adopts the discovered engine and reaches ready without spawning', async ({ harness }) => {
  await expectEngineState(harness.window, 'ready')
  await expect(harness.window.locator('.engine-pill')).toContainText('adopted')
  // The adopt path authenticated against health and the mock saw no spawn-side
  // traffic beyond it; the boot-race ("engine is not ready" while the renderer
  // mounts before readiness) stayed non-fatal: no failure banner, and the
  // Library view settles into its empty-state CTA.
  await expect(harness.window.locator('.error-banner')).toBeHidden()
  await expect(harness.window.locator('.empty-state')).toContainText(
    'Transcribe your first episode'
  )
  const log = await harness.mock.log()
  expect(log.some((entry) => entry.detail === 'GET /v1/health')).toBe(true)
  expect(log.some((entry) => entry.kind === 'events-open')).toBe(true)
})

test('window close runs the quit sequence: events closed, then shutdown, then exit', async ({
  harness
}) => {
  await expectEngineState(harness.window, 'ready')
  const exited = appExited(harness.app)
  // The user-quit path is window close — never a signal (Electron's signal
  // handling under xvfb is not the production path).
  await closeAllWindows(harness.app)
  await exited

  const log = await harness.mock.logFromFile()
  const seqOf = (predicate: (entry: { kind: string }) => boolean): number =>
    log.find(predicate)?.seq ?? -1
  const eventsClose = seqOf((entry) => entry.kind === 'events-close')
  const shutdown = seqOf((entry) => entry.kind === 'shutdown')
  const exit = seqOf((entry) => entry.kind === 'exit')
  // Per P1: the app aborts its own /v1/events stream BEFORE posting shutdown,
  // and the shutdown POST lands before the engine (and then the app) exits.
  expect(eventsClose).toBeGreaterThan(0)
  expect(shutdown).toBeGreaterThan(eventsClose)
  expect(exit).toBeGreaterThan(shutdown)
})

test('stale discovery + failing spawn surfaces structured startup failure', async () => {
  const dataDir = await mkdtemp(join(tmpdir(), 'pr-e2e-stale-'))
  const userDataDir = await mkdtemp(join(tmpdir(), 'pr-e2e-staleuser-'))
  try {
    // A discovery file naming a DEAD pid (a child that already exited), plus
    // an engine command that fails fast: the app must kill/respawn through
    // the spawn chain and surface the captured stderr — never hang.
    const dead = spawn(process.execPath, ['-e', 'process.exit(0)'])
    const deadPid = dead.pid ?? 0
    await new Promise<void>((resolve) => dead.once('exit', () => resolve()))
    await writeFile(
      join(dataDir, 'engine-state.json'),
      JSON.stringify({ port: 1, token: 'stale-token' }),
      { mode: 0o600 }
    )
    await writeFile(
      join(dataDir, 'engine.json'),
      JSON.stringify({
        port: 1,
        pid: deadPid,
        token_fingerprint: tokenFingerprint('stale-token'),
        version: '0.3.0'
      }),
      { mode: 0o600 }
    )
    const failScript = join(dataDir, 'fail-engine.cjs')
    await writeFile(
      failScript,
      "console.error('mock engine exploded on purpose'); process.exit(7);\n"
    )

    const app = await launchApp({
      dataDir,
      userDataDir,
      extraEnv: { PODCAST_READER_ENGINE_CMD: `${process.execPath} ${failScript}` }
    })
    try {
      const window = await app.firstWindow()
      await expectEngineState(window, 'failed')
      const banner = window.locator('.error-banner')
      await expect(banner).toBeVisible()
      await expect(banner).toContainText('Engine failed to start')
      // The structured EngineStartupError carries the child's captured stderr.
      await expect(banner).toContainText('mock engine exploded on purpose')
    } finally {
      await app.close()
    }
  } finally {
    await rm(dataDir, { recursive: true, force: true })
    await rm(userDataDir, { recursive: true, force: true })
  }
})
