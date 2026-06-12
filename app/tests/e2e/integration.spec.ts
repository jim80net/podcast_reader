import { spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

import { test as base, expect } from '@playwright/test'

import { APP_DIR, appExited, closeAllWindows, expectEngineState, launchApp } from './fixtures'
import type {
  DiscoveryInfo,
  EngineSettings,
  EngineState,
  HardwareInfo,
  JobRecord,
  LibraryEntry,
  PacksResponse,
  PackStatus
} from '../../src/shared/types'

/**
 * Real-engine spawn smoke (task 7.3, integration-marked — the `integration`
 * Playwright project). Launches the app with the dev fallback
 * (`uv run podcast-reader serve` from the repo root), proving the TS
 * handshake mirror against `engine/process.py` reality: sentinel →
 * discovery file → authed health → Library renders.
 *
 * Additionally (per Q4): EXACT key-set equality of live engine payloads —
 * JobRecord / LibraryEntry / EngineSettings, plus the handshake files —
 * against the `src/shared/types.ts` mirrors, so mirror drift trips CI
 * instead of users.
 *
 * Requires the repo's Python toolchain (`uv sync --extra dev` at the root).
 */

const REPO_ROOT = resolve(APP_DIR, '..')

// Record<keyof T, true> forces these literals to stay exhaustive AND
// extra-free at compile time; the runtime assertion compares against reality.
const ENGINE_SETTINGS_KEYS: Record<keyof EngineSettings, true> = {
  whisper_model: true,
  whisper_lang: true,
  whisper_device: true,
  sentences: true,
  library_dir: true,
  chapter_model: true,
  chapter_provider: true,
  custom_provider_url: true
}
const JOB_RECORD_KEYS: Record<keyof JobRecord, true> = {
  id: true,
  source: true,
  title: true,
  state: true,
  error: true,
  events: true,
  result: true,
  created_at: true,
  updated_at: true
}
const LIBRARY_ENTRY_KEYS: Record<keyof LibraryEntry, true> = {
  source_id: true,
  source: true,
  title: true,
  html_path: true,
  created_at: true
}
const DISCOVERY_KEYS: Record<keyof DiscoveryInfo, true> = {
  port: true,
  pid: true,
  token_fingerprint: true,
  version: true
}
const ENGINE_STATE_KEYS: Record<keyof EngineState, true> = {
  port: true,
  token: true
}
const PACKS_RESPONSE_KEYS: Record<keyof PacksResponse, true> = {
  hardware: true,
  packs: true
}
const HARDWARE_INFO_KEYS: Record<keyof HardwareInfo, true> = {
  platform: true,
  nvidia_gpu: true,
  gpu_names: true
}
const PACK_STATUS_KEYS: Record<keyof PackStatus, true> = {
  id: true,
  kind: true,
  display_name: true,
  size: true,
  state: true,
  recommended: true,
  installed_version: true,
  progress: true,
  error: true
}

function expectKeySetEquality(payload: object, mirror: Record<string, true>, label: string): void {
  expect(Object.keys(payload).sort(), `${label} key-set drift vs src/shared/types.ts`).toEqual(
    Object.keys(mirror).sort()
  )
}

const test = base

test('real engine: dev-fallback spawn, handshake, key-set parity, clean quit', async () => {
  test.setTimeout(180_000)
  const dataDir = await mkdtemp(join(tmpdir(), 'pr-int-data-'))
  const userDataDir = await mkdtemp(join(tmpdir(), 'pr-int-user-'))
  // Pre-set the app-side first-run flag: a fresh data dir has no packs, so
  // the setup wizard would otherwise auto-open and replace the Library view
  // this smoke asserts on. The wizard has its own mock-engine coverage.
  await writeFile(
    join(userDataDir, 'app-config.json'),
    JSON.stringify({ first_run_complete: true })
  )
  const app = await launchApp({
    dataDir,
    userDataDir,
    // Force the dev fallback (`uv run podcast-reader serve`): no override,
    // no packaged engine, empty data dir → spawn, never adopt.
    dropEnv: ['PODCAST_READER_ENGINE_CMD']
  })
  try {
    const window = await app.firstWindow()

    // Sentinel → discovery → authed health all happened iff we reach ready
    // as a SPAWNED engine (the pill omits the "(adopted)" suffix).
    await expectEngineState(window, 'ready')
    await expect(window.locator('.engine-pill')).not.toContainText('adopted')
    await expect(window.locator('.empty-state')).toContainText(
      'Transcribe your first episode'
    )

    // The handshake files the app consumed, now checked against the mirrors.
    const discovery = JSON.parse(
      await readFile(join(dataDir, 'engine.json'), 'utf8')
    ) as DiscoveryInfo
    const engineState = JSON.parse(
      await readFile(join(dataDir, 'engine-state.json'), 'utf8')
    ) as EngineState
    expectKeySetEquality(discovery, DISCOVERY_KEYS, 'DiscoveryInfo')
    expectKeySetEquality(engineState, ENGINE_STATE_KEYS, 'EngineState')

    const engine = async (path: string, init: RequestInit = {}): Promise<Response> => {
      const res = await fetch(`http://127.0.0.1:${discovery.port}${path}`, {
        ...init,
        headers: {
          authorization: `Bearer ${engineState.token}`,
          'content-type': 'application/json'
        }
      })
      expect(res.ok, `${path} -> ${res.status}`).toBe(true)
      return res
    }

    // EngineSettings parity.
    const settings = (await (await engine('/v1/settings')).json()) as EngineSettings
    expectKeySetEquality(settings, ENGINE_SETTINGS_KEYS, 'EngineSettings')

    // Pack payload parity (task 6.4): the wizard/Settings hydration source.
    const packsResponse = (await (await engine('/v1/packs')).json()) as PacksResponse
    expectKeySetEquality(packsResponse, PACKS_RESPONSE_KEYS, 'PacksResponse')
    expectKeySetEquality(packsResponse.hardware, HARDWARE_INFO_KEYS, 'HardwareInfo')
    expect(packsResponse.packs.length).toBeGreaterThan(0)
    for (const pack of packsResponse.packs) {
      expectKeySetEquality(pack, PACK_STATUS_KEYS, `PackStatus(${pack.id})`)
    }
    // The unpublished diarization pack reports `unavailable` (per S5).
    expect(packsResponse.packs.find((pack) => pack.id === 'diarization')?.state).toBe(
      'unavailable'
    )

    // JobRecord parity — awaiting-confirmation so no pipeline step ever runs,
    // then discarded to leave the journal clean.
    const job = (await (
      await engine('/v1/jobs', {
        method: 'POST',
        body: JSON.stringify({
          source: 'https://example.com/keyset-probe',
          requires_confirmation: true
        })
      })
    ).json()) as JobRecord
    expectKeySetEquality(job, JOB_RECORD_KEYS, 'JobRecord')
    expect(job.state).toBe('awaiting-confirmation')
    await engine(`/v1/jobs/${job.id}`, { method: 'DELETE' })

    // LibraryEntry parity — seeded through the engine's own library module
    // (a real engine-authored index entry, not a hand-rolled fixture).
    const seed = spawnSync(
      'uv',
      [
        'run',
        'python',
        '-c',
        [
          'import sys, time',
          'from pathlib import Path',
          'from podcast_reader.engine.library import add_entry',
          'from podcast_reader.types import LibraryEntry',
          'lib = Path(sys.argv[1])',
          "html = lib / 'seeded' / 'seeded.html'",
          'html.parent.mkdir(parents=True, exist_ok=True)',
          "html.write_text('<html><body>seeded</body></html>')",
          "add_entry(lib, LibraryEntry(source_id='seeded', source='https://example.com/seeded', title='Seeded Episode', html_path=str(html), created_at=time.time()))"
        ].join('\n'),
        settings.library_dir
      ],
      { cwd: REPO_ROOT, encoding: 'utf8' }
    )
    expect(seed.status, seed.stderr).toBe(0)
    const entries = (await (await engine('/v1/library')).json()) as LibraryEntry[]
    expect(entries).toHaveLength(1)
    expectKeySetEquality(entries[0] ?? {}, LIBRARY_ENTRY_KEYS, 'LibraryEntry')

    // Clean quit against the REAL engine: window close → shutdown POST →
    // serve_engine's finally removes the discovery file; no engine survives.
    const enginePid = discovery.pid
    const exited = appExited(app, 30_000)
    await closeAllWindows(app)
    await exited
    await expect
      .poll(() => existsSync(join(dataDir, 'engine.json')), { timeout: 10_000 })
      .toBe(false)
    // The engine process finishes its own teardown (store shutdown, socket
    // close) shortly after removing the discovery file — poll, don't snapshot.
    await expect
      .poll(
        () => {
          try {
            process.kill(enginePid, 0)
            return 'alive'
          } catch {
            return 'gone'
          }
        },
        { timeout: 10_000 }
      )
      .toBe('gone')
  } finally {
    try {
      await app.close()
    } catch {
      // already exited via the quit sequence
    }
    await rm(dataDir, { recursive: true, force: true })
    await rm(userDataDir, { recursive: true, force: true })
  }
})
