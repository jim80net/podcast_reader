/**
 * Installed-app walkthrough (venture increment W2).
 *
 * Drives the INSTALLED Podcast Reader — the NSIS-installed exe with its real
 * frozen engine, nothing mocked — through the first-run path and captures
 * screenshots as CI artifacts:
 *
 *   01-first-run-wizard.png   wizard (its appearance itself requires an
 *                             engine `ready` status — main.ts gates it)
 *   02-new-view-submitted.png New view right after submitting a YouTube URL
 *   03-new-view-job-done.png  a finished local-audio job card
 *   04-reader-transcript.png  Reader with the rendered transcript
 *
 * GitHub-hosted Windows runners are routinely blocked by YouTube, so the
 * submitted YouTube job is intentionally not the source of the Reader proof.
 * After capturing that real submission, this walkthrough installs the real,
 * pinned tiny Whisper pack and transcribes the repository's short speech WAV.
 * No engine or IPC boundary is mocked.
 *
 * The engine handshake is asserted explicitly via window.api.getEngineStatus
 * (state === 'ready', non-empty version, adopted === false — i.e. the app
 * SPAWNED the bundled frozen engine rather than adopting a dev one).
 *
 * The renderer is credential-free by design (preload is its only door), so
 * captures cannot embed the bearer token; nothing sensitive renders in the
 * captured views.
 *
 * Usage: node tests/install/walkthrough.mjs --exe <installed-exe> --out <dir>
 * Run from app/ (playwright resolves from app/node_modules). Exits non-zero
 * on any failed step, leaving a failure screenshot + console dump in --out.
 *
 * Local dev smoke (not what CI runs): pass --main out/main/index.js with
 * --exe node_modules/electron/dist/electron and isolate state via
 * PODCAST_READER_DATA_DIR / PODCAST_READER_USER_DATA_DIR — the dev spawn
 * chain then runs the engine via `uv run podcast-reader serve`.
 */
import { mkdir, writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import { parseArgs } from 'node:util'
import { _electron } from 'playwright'

// A short, famously stable video with English captions ("Me at the zoo",
// 19 s) — the keyless captions path needs no packs and no API key.
const YOUTUBE_URL = 'https://www.youtube.com/watch?v=jNQXAC9IVRw'

const READY_TIMEOUT_MS = 120_000
const WIZARD_TIMEOUT_MS = 30_000
const PACK_TIMEOUT_MS = 180_000
const JOB_TIMEOUT_MS = 420_000

const { values: args } = parseArgs({
  options: {
    exe: { type: 'string' },
    out: { type: 'string' },
    main: { type: 'string' } // dev smoke only; CI launches the installed exe
  }
})
if (!args.exe || !args.out) {
  console.error('usage: node walkthrough.mjs --exe <installed-exe> --out <dir> [--main <entry>]')
  process.exit(2)
}
const testAudio = process.env.PODCAST_READER_TEST_AUDIO
if (!testAudio) {
  console.error('PODCAST_READER_TEST_AUDIO must point to a short local audio fixture')
  process.exit(2)
}
const outDir = args.out
await mkdir(outDir, { recursive: true })

const consoleLines = []
let app
let page

function log(message) {
  console.log(`[walkthrough] ${message}`)
}

async function fail(message) {
  console.error(`[walkthrough] FAIL: ${message}`)
  try {
    if (page) await page.screenshot({ path: join(outDir, 'ZZ-failure.png') })
  } catch {
    /* window may be gone */
  }
  await writeFile(join(outDir, 'renderer-console.log'), consoleLines.join('\n'))
  process.exit(1)
}

async function waitFor(predicate, timeoutMs, what) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    let result
    try {
      result = await predicate()
    } catch {
      result = undefined // renderer mid-navigation; retry
    }
    if (result) return result
    await new Promise((resolve) => setTimeout(resolve, 500))
  }
  await fail(`timed out after ${timeoutMs}ms waiting for ${what}`)
}

log(`launching ${args.main ? 'dev build' : 'installed app'}: ${args.exe}`)
app = await _electron.launch({
  executablePath: args.exe,
  args: args.main ? [args.main] : []
})
page = await app.firstWindow()
page.on('console', (msg) => consoleLines.push(`[${msg.type()}] ${msg.text()}`))

// --- 1. engine handshake: the supervised frozen engine reaches `ready` -----
const status = await waitFor(
  async () => {
    const s = await page.evaluate(() => window.api.getEngineStatus())
    return s.state === 'ready' ? s : undefined
  },
  READY_TIMEOUT_MS,
  'engine status ready'
)
if (!status.version) await fail(`ready status carries no engine version: ${JSON.stringify(status)}`)
if (status.adopted) await fail('engine was ADOPTED — expected the installed app to spawn its own')
log(`engine ready: v${status.version} (spawned)`)

// --- 2. first-run wizard (auto-opens: first run + ready + packs missing) ---
await waitFor(
  () => page.evaluate(() => location.hash === '#/setup'),
  WIZARD_TIMEOUT_MS,
  'first-run wizard route'
)
await page.locator('.setup-title').waitFor({ timeout: WIZARD_TIMEOUT_MS })
// Let the hardware/pack sections finish their first load before capturing.
await page
  .locator('.pack-list [role="listitem"], .pack-list li, .pack-list > *')
  .first()
  .waitFor({ timeout: WIZARD_TIMEOUT_MS })
  .catch(() => log('pack list did not render items; capturing wizard as-is'))
await page.screenshot({ path: join(outDir, '01-first-run-wizard.png') })
log('captured first-run wizard')

// Captions need no packs: "Skip for now" marks first-run complete and lands
// on the Library (the Finish button stays hidden until packs install).
await page.locator('#setup-skip').click()
await waitFor(
  () => page.evaluate(() => location.hash !== '#/setup'),
  WIZARD_TIMEOUT_MS,
  'wizard to complete'
)

// --- 3. New view: submit the YouTube URL (keyless captions job) ------------
await page.evaluate(() => {
  location.hash = '#/new'
})
const urlInput = page.locator('.new-source-input')
await urlInput.waitFor({ timeout: WIZARD_TIMEOUT_MS })
await urlInput.fill(YOUTUBE_URL)
await page.getByRole('button', { name: 'Transcribe' }).click()
await page.locator('.job-card').first().waitFor({ timeout: WIZARD_TIMEOUT_MS })
await page.screenshot({ path: join(outDir, '02-new-view-submitted.png') })
log('captured New view with submitted job')

// GitHub runner IPs are often blocked by YouTube. Keep the real captions
// submission above as a product/UI proof, then use the installed app's pack
// manager and frozen Whisper worker for a deterministic Reader proof.
await page.evaluate(() => window.api.installPack('model-tiny'))
await waitFor(
  async () => {
    const response = await page.evaluate(() => window.api.listPacks())
    const pack = response.packs.find((candidate) => candidate.id === 'model-tiny')
    if (pack?.state === 'failed' || pack?.state === 'incompatible') {
      await fail(`tiny Whisper pack ${pack.state}: ${JSON.stringify(pack.error)}`)
    }
    return pack?.state === 'installed' ? pack : undefined
  },
  PACK_TIMEOUT_MS,
  'tiny Whisper pack to install'
)
log('tiny Whisper pack installed')

const settings = await page.evaluate(() => window.api.getSettings())
await page.evaluate(
  (nextSettings) => window.api.putSettings(nextSettings),
  { ...settings, whisper_model: 'tiny', whisper_device: 'cpu' }
)

await urlInput.fill(testAudio)
await page.locator('#new-title').fill('Installed app audio proof')
await page.getByRole('button', { name: 'Transcribe' }).click()
await waitFor(
  async () => {
    const jobs = await page.evaluate(() => window.api.listJobs())
    const localJob = jobs.find((job) => job.source === testAudio)
    if (localJob?.state === 'failed' || localJob?.state === 'interrupted') {
      await fail(`local audio job ${localJob.state}: ${JSON.stringify(localJob.error)}`)
    }
    return localJob?.state === 'done' ? localJob : undefined
  },
  JOB_TIMEOUT_MS,
  'local audio transcription to finish'
)

// --- 4. open the real transcript produced by the installed frozen engine ---
const readerHref = await waitFor(
  async () => {
    const entries = await page.evaluate(() => window.api.listLibrary())
    const entry = entries.find((candidate) => candidate.source === testAudio)
    return entry === undefined ? undefined : `#/reader/${entry.source_id}`
  },
  WIZARD_TIMEOUT_MS,
  'finished local-audio transcript to appear in the library'
)
await page.screenshot({ path: join(outDir, '03-new-view-job-done.png') })
log(`job done, transcript at ${readerHref}`)

// --- 5. Reader: the transcript renders inside the sandboxed iframe ---------
await page.evaluate((href) => {
  location.hash = href
}, readerHref)
const readerFrame = page.locator('iframe.reader-frame')
await readerFrame.waitFor({ timeout: WIZARD_TIMEOUT_MS })
await page
  .frameLocator('iframe.reader-frame')
  .locator('p')
  .first()
  .waitFor({ timeout: WIZARD_TIMEOUT_MS })
await page.screenshot({ path: join(outDir, '04-reader-transcript.png') })
log('captured Reader with rendered transcript')

await writeFile(join(outDir, 'renderer-console.log'), consoleLines.join('\n'))
await app.close()
log('walkthrough complete')
