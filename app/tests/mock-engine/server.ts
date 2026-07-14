/**
 * Mock engine (task 7.1, design decision 11): a scriptable HTTP server for
 * the engine's `/v1` surface, run as a SEPARATE PROCESS so the app's
 * production adopt path works against it unmodified — the Playwright fixture
 * writes `engine-state.json` + `engine.json` (this process's real PID and
 * port) into a temp PODCAST_READER_DATA_DIR, and the app's PID-liveness,
 * fingerprint, health, and quit-sequence checks all hold for real.
 *
 * Honoring the real handshake includes dying for real: `POST /v1/shutdown`
 * answers 202 and then exits this process, exactly like
 * `engine/process.py:serve_engine` — the app's bounded PID-wait observes a
 * genuine exit.
 *
 * Scripting/observation happens on the unauthenticated `/__mock/*` namespace
 * (localhost-only, test-runner-only). An ordered event log (events-open /
 * events-close / request lines / shutdown / exit) is appended synchronously
 * to MOCK_LOG_FILE so post-exit assertions (quit ordering, per P1) survive
 * the process.
 *
 * Runs directly under Node >= 23.6 type stripping: erasable TS only, no
 * imports beyond node builtins.
 *
 * Env: MOCK_ENGINE_TOKEN (required bearer token), MOCK_LOG_FILE (optional).
 * Stdout: one ready line `MOCK_ENGINE_READY {"port":N,"pid":N}`.
 */
import { createHash } from 'node:crypto'
import { appendFileSync, writeFileSync } from 'node:fs'
import { createServer } from 'node:http'
import { join } from 'node:path'
import type { IncomingMessage, ServerResponse } from 'node:http'

// ---- mirrored payload shapes (kept structurally equal to src/shared/types.ts) --

interface JobError {
  code: string
  message: string
  hint: string
  detail: string
}

interface PipelineEvent {
  kind: string
  step: string | null
  message: string
  data: Record<string, unknown>
}

interface JobRecord {
  id: string
  source: string
  title: string | null
  state: string
  error: JobError | null
  events: PipelineEvent[]
  result: Record<string, unknown> | null
  overrides: Record<string, unknown> | null
  models: Record<string, unknown> | null
  created_at: number
  updated_at: number
}

interface LibraryEntry {
  source_id: string
  source: string
  title: string
  html_path: string
  created_at: number
}

interface EngineSettings {
  whisper_model: string
  whisper_lang: string
  whisper_device: string
  sentences: number
  library_dir: string
  chapter_model: string
  chapter_provider: string
  custom_provider_url: string
  custom_providers: Array<{
    name: string
    base_url: string
    default_model: string
    max_tokens: number
  }>
  diarize: boolean
  caption_cleanup: boolean
  media_cache_max_bytes: number
}

interface PackProgress {
  bytes: number
  total: number
}

interface PackInstallError {
  code: string
  message: string
}

interface HardwareInfo {
  platform: string
  nvidia_gpu: boolean
  gpu_names: string[]
}

interface LicenseNotice {
  name: string
  text: string
}

interface PackStatus {
  id: string
  kind: string
  display_name: string
  size: number
  state: string
  recommended: boolean
  installed_version: string | null
  progress: PackProgress | null
  error: PackInstallError | null
  licenses: LicenseNotice[]
}

interface MediaInfo {
  kind: string
  youtube_id: string
  duration_s: number
  status: string
  progress: number
}

/** A scripted media entry: its info plus the bytes the media route serves. */
interface MediaEntry {
  info: MediaInfo
  bytes: Buffer
  contentType: string
}

// ---- state ------------------------------------------------------------------

const token = process.env.MOCK_ENGINE_TOKEN ?? ''
if (token === '') {
  console.error('MOCK_ENGINE_TOKEN is required')
  process.exit(2)
}
const logFile = process.env.MOCK_LOG_FILE

const PROVIDERS = ['anthropic', 'openai', 'xai', 'openrouter', 'deepseek', 'custom']

const jobs = new Map<string, JobRecord>()
const library: LibraryEntry[] = []
const transcripts = new Map<string, string>()
let settings: EngineSettings = {
  whisper_model: 'large-v3',
  whisper_lang: 'en',
  whisper_device: 'cuda',
  sentences: 5,
  library_dir: '/tmp/mock-library',
  chapter_model: '',
  chapter_provider: 'anthropic',
  custom_provider_url: '',
  custom_providers: [],
  diarize: false,
  caption_cleanup: false,
  media_cache_max_bytes: 5 * 1024 ** 3
}
let keyTestResult: { ok: boolean; detail: string | null } = { ok: true, detail: null }
let keyTestDelayMs = 0
let settingsPutDelayMs = 0
const pushedKeys = new Set<string>()

// Pack surface defaults: recommended packs ALREADY installed, so the setup
// wizard's first-run trigger ("recommended packs missing") stays quiet in
// every test that doesn't seed pack state explicitly. Wizard/pack tests seed
// not-installed states (and hardware) through /__mock/seed before launch.
let hardware: HardwareInfo = {
  platform: 'win32',
  nvidia_gpu: true,
  gpu_names: ['Mock GeForce RTX']
}

function makePack(partial: Partial<PackStatus> & { id: string }): PackStatus {
  return {
    kind: 'model',
    display_name: partial.id,
    size: 0,
    state: 'not-installed',
    recommended: false,
    installed_version: null,
    progress: null,
    error: null,
    licenses: [],
    ...partial
  }
}

/** Mirrors the engine registry's ids/kinds/sizes (engine/packs.py REGISTRY). */
function defaultPacks(): Map<string, PackStatus> {
  const entries: PackStatus[] = [
    makePack({
      id: 'cuda-runtime',
      kind: 'runtime',
      display_name: 'NVIDIA CUDA runtime (cuBLAS + cuDNN 9)',
      size: 1_243_159_663,
      state: 'installed',
      recommended: true,
      installed_version: '1',
      licenses: [
        { name: 'NVIDIA cuBLAS', text: 'Mock cuBLAS attribution notice.' },
        { name: 'NVIDIA cuDNN', text: 'Mock cuDNN attribution notice.' }
      ]
    }),
    makePack({ id: 'model-tiny', display_name: 'Whisper tiny model', size: 78_203_619 }),
    makePack({ id: 'model-small', display_name: 'Whisper small model', size: 486_212_372 }),
    makePack({ id: 'model-medium', display_name: 'Whisper medium model', size: 1_530_571_735 }),
    makePack({
      id: 'model-large-v3',
      display_name: 'Whisper large-v3 model',
      size: 3_090_835_702,
      state: 'installed',
      recommended: true,
      installed_version: 'mock-rev',
      licenses: [{ name: 'MIT (Systran faster-whisper)', text: 'Mock model attribution notice.' }]
    }),
    makePack({
      id: 'diarization',
      kind: 'worker',
      display_name: 'Speaker diarization worker',
      state: 'unavailable'
    })
  ]
  return new Map(entries.map((pack) => [pack.id, pack]))
}

const packs = defaultPacks()
let jobCounter = 0
const sseClients = new Set<ServerResponse>()

// Pairing state mirrors engine/pairing.py: single pending code, 300 s TTL,
// 5-failed-attempt budget, single-use, replaced on every mint. Tests seed a
// known code via /__mock/seed; /__mock/pairing reads the pending one.
const PAIR_CODE_ALPHABET = '23456789ABCDEFGHJKMNPQRSTVWXYZ'
const PAIR_CODE_TTL_S = 300
const PAIR_MAX_FAILED_ATTEMPTS = 5
let pairingCode: string | null = null
let pairingExpiresAt = 0
let pairingFailedAttempts = 0

// Cookie jars mirror engine/cookies.py storage semantics in memory: domain →
// jar + created_at. /v1 listing is metadata-only like the real engine; the
// /__mock/cookies seam exposes jar content to the test runner only.
const cookieJars = new Map<string, { jar: string; created_at: number }>()

// Media entries (floating-video-player): source_id → {info, bytes}. Seeded via
// /__mock/seed `media`. The byte payloads are tiny placeholders — enough for
// the player to mount and for Range to be exercised (the browser never decodes
// them in the kept e2e assertions; click-to-seek/highlight drive sync directly).
const media = new Map<string, MediaEntry>()

// A minimal mp4 ftyp box + an mp3 frame header are enough placeholder bytes.
const FIXTURE_MP4 = Buffer.from('0000001c66747970697336320000020069736f32617663316d703431', 'hex')
const FIXTURE_MP3 = Buffer.from('fffb90c40000000000000000000000000000000000000000', 'hex')

function seedMedia(sourceId: string, kind: string, partial: Partial<MediaInfo> = {}): void {
  const isYoutube = kind === 'youtube'
  const isAudio = kind === 'audio'
  media.set(sourceId, {
    info: {
      kind,
      youtube_id: isYoutube ? (partial.youtube_id ?? 'dQw4w9WgXcQ') : '',
      duration_s: partial.duration_s ?? 30,
      status: partial.status ?? (kind === 'unavailable' ? 'unavailable' : 'ready'),
      progress: partial.progress ?? 1
    },
    bytes: isYoutube ? Buffer.alloc(0) : isAudio ? FIXTURE_MP3 : FIXTURE_MP4,
    contentType: isAudio ? 'audio/mpeg' : 'video/mp4'
  })
}

interface LogEntry {
  seq: number
  kind: string
  detail: string
}
const log: LogEntry[] = []
let seq = 0

function record(kind: string, detail = ''): void {
  const entry: LogEntry = { seq: (seq += 1), kind, detail }
  log.push(entry)
  if (logFile !== undefined) appendFileSync(logFile, `${JSON.stringify(entry)}\n`)
}

// ---- helpers ------------------------------------------------------------------

function sendJson(res: ServerResponse, status: number, payload: unknown): void {
  const body = JSON.stringify(payload)
  res.writeHead(status, { 'content-type': 'application/json' })
  res.end(body)
}

function detail(res: ServerResponse, status: number, message: string): void {
  sendJson(res, status, { detail: message })
}

async function readBody(req: IncomingMessage): Promise<Record<string, unknown>> {
  const chunks: Buffer[] = []
  for await (const chunk of req) chunks.push(chunk as Buffer)
  const text = Buffer.concat(chunks).toString('utf8')
  if (text === '') return {}
  return JSON.parse(text) as Record<string, unknown>
}

function nowSeconds(): number {
  return Date.now() / 1000
}

function makeJob(
  source: string,
  title: string | null,
  state: string,
  overrides: Record<string, unknown> | null = null
): JobRecord {
  jobCounter += 1
  const job: JobRecord = {
    id: `mock-job-${jobCounter}`,
    source,
    title,
    state,
    error: null,
    events: [],
    result: null,
    overrides,
    models: null,
    created_at: nowSeconds(),
    updated_at: nowSeconds()
  }
  jobs.set(job.id, job)
  return job
}

function broadcast(event: PipelineEvent): void {
  const frame = `data: ${JSON.stringify(event)}\n\n`
  for (const client of sseClients) client.write(frame)
}

function validateSettingsPut(body: Record<string, unknown>): string | null {
  const customProviders =
    (body.custom_providers as EngineSettings['custom_providers'] | undefined) ??
    settings.custom_providers
  const providerIds = [...PROVIDERS, ...customProviders.map((entry) => entry.name)]
  const provider = (body.chapter_provider as string | undefined) ?? settings.chapter_provider
  if (!providerIds.includes(provider)) return `unknown chapter provider: '${provider}'`
  const url = (body.custom_provider_url as string | undefined) ?? settings.custom_provider_url
  if (provider === 'custom' || url !== '') {
    if (url === '') {
      return (
        'custom provider requires a base URL ' +
        '(set custom_provider_url / PODCAST_READER_CUSTOM_PROVIDER_URL)'
      )
    }
    const ok =
      url.startsWith('https://') ||
      url.startsWith('http://localhost') ||
      url.startsWith('http://127.0.0.1')
    if (!ok) return 'custom provider base URL must be https, or http on localhost/127.0.0.1'
  }
  return null
}

function mintPairingCode(code?: string, ttlS = PAIR_CODE_TTL_S): { code: string; expires_at: number } {
  pairingCode =
    code ??
    Array.from(
      { length: 6 },
      () => PAIR_CODE_ALPHABET[Math.floor(Math.random() * PAIR_CODE_ALPHABET.length)]
    ).join('')
  pairingExpiresAt = nowSeconds() + ttlS
  pairingFailedAttempts = 0
  return { code: pairingCode, expires_at: pairingExpiresAt }
}

function claimPairingCode(code: string): boolean {
  if (pairingCode === null || nowSeconds() >= pairingExpiresAt) {
    pairingCode = null
    return false
  }
  if (code !== pairingCode) {
    pairingFailedAttempts += 1
    if (pairingFailedAttempts >= PAIR_MAX_FAILED_ATTEMPTS) pairingCode = null
    return false
  }
  pairingCode = null // single-use
  return true
}

/**
 * The engine's single unauthenticated route, mirrored exactly (per U3/U5):
 * POST only, JSON content type required, http/https Origin rejected
 * (chrome-extension:// passes), gate rejections never burn the attempt
 * budget, and every rejection is the same self-authored 403.
 */
async function handlePairClaim(req: IncomingMessage, res: ServerResponse): Promise<void> {
  record('pair-claim-attempt')
  const reject = (): void => detail(res, 403, 'pairing claim rejected')
  const mediaType = (req.headers['content-type'] ?? '').split(';')[0]?.trim().toLowerCase()
  if (mediaType !== 'application/json') return reject()
  const originScheme = (req.headers.origin ?? '').split(':')[0]?.trim().toLowerCase()
  if (originScheme === 'http' || originScheme === 'https') return reject()
  let body: Record<string, unknown>
  try {
    body = await readBody(req)
  } catch {
    return reject()
  }
  const code = body.code
  if (typeof code !== 'string' || !claimPairingCode(code)) return reject()
  sendJson(res, 200, { token })
}

/** Lightweight mirror of engine/cookies.py validate_jar (suffix-match, per U4). */
function validateJar(domain: string, jar: string): string | null {
  if (!/^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/.test(domain)) {
    return 'domain must be a bare lowercase hostname (e.g. example.com)'
  }
  if (Buffer.byteLength(jar) > 1024 * 1024) return 'cookie jar exceeds the 1 MB size cap'
  let cookieLines = 0
  const lines = jar.split('\n')
  for (let i = 0; i < lines.length; i += 1) {
    let line = lines[i] ?? ''
    if (line.endsWith('\r')) line = line.slice(0, -1)
    if (line.startsWith('#HttpOnly_')) line = line.slice('#HttpOnly_'.length)
    else if (line.trim() === '' || line.startsWith('#')) continue
    const fields = line.split('\t')
    if (fields.length !== 7) {
      return `line ${i + 1}: not a Netscape cookie line (expected 7 tab-separated fields)`
    }
    const cookieDomain = (fields[0] ?? '').toLowerCase().replace(/^\./, '')
    if (cookieDomain !== domain && !cookieDomain.endsWith(`.${domain}`)) {
      return `line ${i + 1}: cookie domain does not match the declared domain '${domain}'`
    }
    cookieLines += 1
  }
  if (cookieLines === 0) return 'cookie jar contains no cookie lines'
  return null
}

// ---- /__mock control surface ---------------------------------------------------

async function handleControl(
  req: IncomingMessage,
  res: ServerResponse,
  path: string
): Promise<void> {
  if (req.method === 'GET' && path === '/__mock/log') {
    sendJson(res, 200, log)
    return
  }
  if (req.method === 'POST' && path === '/__mock/seed') {
    const body = await readBody(req)
    for (const job of (body.jobs as JobRecord[] | undefined) ?? []) jobs.set(job.id, job)
    for (const entry of (body.library as LibraryEntry[] | undefined) ?? []) library.push(entry)
    for (const [id, html] of Object.entries(
      (body.transcripts as Record<string, string> | undefined) ?? {}
    )) {
      transcripts.set(id, html)
    }
    if (body.settings !== undefined) {
      settings = { ...settings, ...(body.settings as Partial<EngineSettings>) }
    }
    if (body.keyTestResult !== undefined) {
      keyTestResult = body.keyTestResult as { ok: boolean; detail: string | null }
    }
    if (body.keyTestDelayMs !== undefined) keyTestDelayMs = Number(body.keyTestDelayMs)
    if (body.settingsPutDelayMs !== undefined) settingsPutDelayMs = Number(body.settingsPutDelayMs)
    if (body.hardware !== undefined) {
      hardware = body.hardware as HardwareInfo
    }
    for (const pack of (body.packs as PackStatus[] | undefined) ?? []) {
      packs.set(pack.id, { ...(packs.get(pack.id) ?? makePack({ id: pack.id })), ...pack })
    }
    if (body.pairing !== undefined) {
      // Script a known pairing code (e2e drives the popup with it).
      const pairing = body.pairing as { code: string; ttlS?: number }
      mintPairingCode(pairing.code, pairing.ttlS ?? PAIR_CODE_TTL_S)
    }
    for (const jar of (body.cookieJars as { domain: string; jar: string }[] | undefined) ?? []) {
      cookieJars.set(jar.domain, { jar: jar.jar, created_at: nowSeconds() })
    }
    // Media entries: { source_id, kind, ...info overrides }[]
    for (const entry of (body.media as
      | ({ source_id: string; kind: string } & Partial<MediaInfo>)[]
      | undefined) ?? []) {
      seedMedia(entry.source_id, entry.kind, entry)
    }
    res.writeHead(204).end()
    return
  }
  if (req.method === 'POST' && path === '/__mock/media-state') {
    // Flip a media entry's prep status and broadcast a media_state event — the
    // seam for the preparing→ready drill (F4). media events carry source_id and
    // NEVER job_id, exactly like pack events.
    const body = await readBody(req)
    const sourceId = body.source_id as string
    const state = body.state as string
    const entry = media.get(sourceId)
    if (entry !== undefined) {
      entry.info.status = state
      entry.info.progress = state === 'ready' ? 1 : entry.info.progress
    }
    broadcast({
      kind: 'media_state',
      step: null,
      message: `media ${state}`,
      data: { source_id: sourceId, state }
    })
    res.writeHead(204).end()
    return
  }
  if (req.method === 'GET' && path === '/__mock/pairing') {
    // The pending code, readable by the test runner only (the real engine
    // never exposes it; e2e needs it to drive the popup's claim form).
    sendJson(res, 200, {
      code: pairingCode,
      expires_at: pairingExpiresAt,
      failed_attempts: pairingFailedAttempts
    })
    return
  }
  if (req.method === 'GET' && path === '/__mock/cookies') {
    // Jar content, test-runner-only: e2e asserts what the extension pushed.
    sendJson(
      res,
      200,
      [...cookieJars.entries()].map(([domain, entry]) => ({ domain, ...entry }))
    )
    return
  }
  if (req.method === 'POST' && path === '/__mock/pack') {
    // Upsert a pack status and optionally broadcast SSE events — the seam for
    // scripting install progress (pack_progress) and state changes
    // (pack_state). Events MUST NOT carry job_id (per Q5).
    const body = await readBody(req)
    const patch = body.pack as Partial<PackStatus> & { id: string }
    const merged = { ...(packs.get(patch.id) ?? makePack({ id: patch.id })), ...patch }
    packs.set(merged.id, merged)
    for (const event of (body.events as PipelineEvent[] | undefined) ?? []) broadcast(event)
    sendJson(res, 200, merged)
    return
  }
  if (req.method === 'POST' && path === '/__mock/job') {
    // Upsert a job record and optionally broadcast SSE events — the seam for
    // scripting job progress (running steps, failures with hints, completion).
    const body = await readBody(req)
    const patch = body.job as Partial<JobRecord> & { id: string }
    const existing = jobs.get(patch.id)
    const merged: JobRecord = {
      ...(existing ?? makeJob(patch.source ?? 'seeded', null, 'queued')),
      ...patch,
      updated_at: nowSeconds()
    }
    jobs.set(merged.id, merged)
    for (const event of (body.events as PipelineEvent[] | undefined) ?? []) {
      merged.events.push(event)
      broadcast(event)
    }
    sendJson(res, 200, merged)
    return
  }
  if (req.method === 'POST' && path === '/__mock/drop-sse') {
    // Sever every open events stream without telling the app — reconnect drill.
    for (const client of sseClients) client.destroy()
    sseClients.clear()
    res.writeHead(204).end()
    return
  }
  if (req.method === 'POST' && path === '/__mock/crash') {
    // A REAL death seam (engine-respawn-supervision L2): exit the process with
    // a non-zero code so a SPAWNED engine's `childExited` resolves and the
    // app's respawn supervision fires — not just an SSE close. Only meaningful
    // when the mock was launched in --spawned mode (the app owns the child).
    record('crash')
    res.writeHead(202).end()
    setTimeout(() => process.exit(1), 20)
    return
  }
  detail(res, 404, `unknown mock control endpoint: ${path}`)
}

// ---- /v1 surface ----------------------------------------------------------------

async function handleV1(req: IncomingMessage, res: ServerResponse, path: string): Promise<void> {
  // (method, path) auth exemptions exactly like the real middleware (per U5):
  // POST /v1/pair/claim and GET /v1/embed/<id> (the tokenless YouTube embed
  // page the Reader iframe loads) bypass the bearer check.
  if (req.method === 'POST' && path === '/v1/pair/claim') {
    return handlePairClaim(req, res)
  }
  if (req.method === 'GET' && path.startsWith('/v1/embed/')) {
    res.writeHead(200, { 'content-type': 'text/html' })
    res.end(
      `<!doctype html><meta charset="utf-8"><script>` +
        `window.parent.postMessage({source:'pr-embed',type:'error',code:150},'*');` +
        `</script>`
    )
    return
  }
  const auth = req.headers.authorization ?? ''
  if (auth !== `Bearer ${token}`) {
    record('unauthorized', `${req.method} ${path}`)
    sendJson(res, 401, { detail: 'unauthorized' })
    return
  }
  record('request', `${req.method} ${path}`)

  if (req.method === 'POST' && path === '/v1/pair') {
    // NEVER log the code — only that a mint happened (engine parity).
    record('pair-mint')
    sendJson(res, 200, mintPairingCode())
    return
  }
  if (req.method === 'PUT' && path === '/v1/cookies') {
    const body = await readBody(req)
    const domain = body.domain as string
    const jar = body.jar as string
    const invalid = validateJar(domain, jar)
    if (invalid !== null) return detail(res, 400, invalid)
    record('cookies-put', domain)
    cookieJars.set(domain, { jar, created_at: nowSeconds() })
    res.writeHead(204).end()
    return
  }
  if (req.method === 'GET' && path === '/v1/cookies') {
    sendJson(
      res,
      200,
      [...cookieJars.keys()]
        .sort()
        .map((domain) => ({ domain, created_at: cookieJars.get(domain)?.created_at ?? 0 }))
    )
    return
  }
  const cookieMatch = /^\/v1\/cookies\/([^/]+)$/.exec(path)
  if (req.method === 'DELETE' && cookieMatch !== null) {
    const domain = decodeURIComponent(cookieMatch[1] ?? '')
    if (!cookieJars.has(domain)) return detail(res, 404, 'no cookie jar stored for that domain')
    record('cookies-delete', domain)
    cookieJars.delete(domain)
    res.writeHead(204).end()
    return
  }

  if (req.method === 'GET' && path === '/v1/health') {
    sendJson(res, 200, { version: '0.3.0', token_fingerprint: fingerprint(token) })
    return
  }
  if (req.method === 'POST' && path === '/v1/shutdown') {
    record('shutdown')
    res.writeHead(202).end()
    // 202-then-exit, like serve_engine: the app's bounded PID-wait sees a
    // real process exit.
    setTimeout(() => {
      record('exit')
      process.exit(0)
    }, 50)
    return
  }
  if (req.method === 'POST' && path === '/v1/jobs') {
    const body = await readBody(req)
    const job = makeJob(
      body.source as string,
      (body.title as string | null | undefined) ?? null,
      body.requires_confirmation === true ? 'awaiting-confirmation' : 'queued',
      (body.overrides as Record<string, unknown> | undefined) ?? null
    )
    sendJson(res, 201, job)
    return
  }
  if (req.method === 'GET' && path === '/v1/jobs') {
    sendJson(res, 200, [...jobs.values()])
    return
  }
  const confirmMatch = /^\/v1\/jobs\/([^/]+)\/confirm$/.exec(path)
  if (req.method === 'POST' && confirmMatch !== null) {
    const job = jobs.get(confirmMatch[1] ?? '')
    if (job === undefined) return detail(res, 404, 'job not found')
    if (job.state !== 'awaiting-confirmation') {
      return detail(res, 409, `job is ${job.state}, not awaiting-confirmation`)
    }
    job.state = 'queued'
    job.updated_at = nowSeconds()
    sendJson(res, 200, job)
    return
  }
  const jobMatch = /^\/v1\/jobs\/([^/]+)$/.exec(path)
  if (jobMatch !== null) {
    const job = jobs.get(jobMatch[1] ?? '')
    if (job === undefined) return detail(res, 404, 'job not found')
    if (req.method === 'GET') {
      sendJson(res, 200, job)
      return
    }
    if (req.method === 'DELETE') {
      if (job.state !== 'awaiting-confirmation') {
        return detail(res, 409, `job is ${job.state}, not awaiting-confirmation`)
      }
      jobs.delete(job.id)
      res.writeHead(204).end()
      return
    }
  }
  if (req.method === 'GET' && path === '/v1/events') {
    record('events-open')
    res.writeHead(200, {
      'content-type': 'text/event-stream',
      'cache-control': 'no-cache'
    })
    res.write(': connected\n\n')
    sseClients.add(res)
    res.on('close', () => {
      sseClients.delete(res)
      record('events-close')
    })
    return
  }
  if (req.method === 'GET' && path === '/v1/packs') {
    sendJson(res, 200, { hardware, packs: [...packs.values()] })
    return
  }
  const installMatch = /^\/v1\/packs\/([^/]+)\/install$/.exec(path)
  if (req.method === 'POST' && installMatch !== null) {
    const pack = packs.get(decodeURIComponent(installMatch[1] ?? ''))
    if (pack === undefined) return detail(res, 404, 'pack not found')
    if (pack.state === 'unavailable') {
      return detail(res, 409, `pack '${pack.id}' has no published artifact yet and cannot be installed`)
    }
    record('pack-install', pack.id)
    // Idempotent like the real engine: installed/installing requests no-op.
    if (pack.state !== 'installed' && pack.state !== 'installing') {
      pack.state = 'installing'
      pack.progress = { bytes: 0, total: pack.size }
      pack.error = null
      broadcast({
        kind: 'pack_state',
        step: null,
        message: `Installing ${pack.display_name}`,
        data: { pack_id: pack.id, state: 'installing' }
      })
    }
    res.writeHead(202).end()
    return
  }
  const packMatch = /^\/v1\/packs\/([^/]+)$/.exec(path)
  if (req.method === 'DELETE' && packMatch !== null) {
    const pack = packs.get(decodeURIComponent(packMatch[1] ?? ''))
    if (pack === undefined) return detail(res, 404, 'pack not found')
    if (pack.state === 'installing') {
      return detail(res, 409, `pack '${pack.id}' is currently installing; uninstall is refused`)
    }
    record('pack-uninstall', pack.id)
    pack.state = 'not-installed'
    pack.installed_version = null
    pack.progress = null
    pack.error = null
    broadcast({
      kind: 'pack_state',
      step: null,
      message: `${pack.display_name} uninstalled`,
      data: { pack_id: pack.id, state: 'not-installed' }
    })
    res.writeHead(204).end()
    return
  }
  if (req.method === 'GET' && path === '/v1/library') {
    sendJson(res, 200, library)
    return
  }
  const mediaInfoMatch = /^\/v1\/media\/([^/]+)\/info$/.exec(path)
  if (req.method === 'GET' && mediaInfoMatch !== null) {
    const entry = media.get(decodeURIComponent(mediaInfoMatch[1] ?? ''))
    const info: MediaInfo =
      entry?.info ?? { kind: 'unavailable', youtube_id: '', duration_s: 0, status: 'unavailable', progress: 0 }
    sendJson(res, 200, info)
    return
  }
  const mediaBytesMatch = /^\/v1\/media\/([^/]+)$/.exec(path)
  if (req.method === 'GET' && mediaBytesMatch !== null) {
    const entry = media.get(decodeURIComponent(mediaBytesMatch[1] ?? ''))
    if (entry === undefined || entry.info.status !== 'ready' || entry.bytes.length === 0) {
      return detail(res, 404, 'media not ready')
    }
    serveBytesWithRange(req, res, entry.bytes, entry.contentType)
    return
  }
  const transcriptMatch = /^\/v1\/transcripts\/(.+)\.html$/.exec(path)
  if (req.method === 'GET' && transcriptMatch !== null) {
    const html = transcripts.get(decodeURIComponent(transcriptMatch[1] ?? ''))
    if (html === undefined) return detail(res, 404, 'transcript not found')
    res.writeHead(200, { 'content-type': 'text/html' })
    res.end(html)
    return
  }
  if (req.method === 'PUT' && path === '/v1/keys') {
    const body = await readBody(req)
    const provider = body.provider as string
    const known = [...PROVIDERS, ...settings.custom_providers.map((entry) => entry.name)]
    if (!known.includes(provider) && (body.api_key as string) !== '') {
      return detail(res, 400, `unknown chapter provider: '${provider}'`)
    }
    // NEVER log the key value — only that a push happened for the provider.
    record('keys-put', provider)
    if ((body.api_key as string) === '') pushedKeys.delete(provider)
    else pushedKeys.add(provider)
    res.writeHead(204).end()
    return
  }
  if (req.method === 'POST' && path === '/v1/keys/test') {
    const body = await readBody(req)
    const known = [...PROVIDERS, ...settings.custom_providers.map((entry) => entry.name)]
    if (!known.includes(body.provider as string)) {
      return detail(res, 400, `unknown chapter provider: '${String(body.provider)}'`)
    }
    record('keys-test', body.provider as string)
    if (keyTestDelayMs > 0) await new Promise((resolve) => setTimeout(resolve, keyTestDelayMs))
    sendJson(res, 200, keyTestResult)
    return
  }
  if (req.method === 'GET' && path === '/v1/providers') {
    sendJson(
      res,
      200,
      [
        ...PROVIDERS.map((id) => ({ id, default_model: id === 'custom' ? '' : `${id}-default-model` })),
        ...settings.custom_providers.map((provider) => ({
          id: provider.name,
          default_model: provider.default_model
        }))
      ].map(({ id, default_model }) => ({
        id,
        default_model,
        key_available: pushedKeys.has(id)
      }))
    )
    return
  }
  if (req.method === 'GET' && path === '/v1/settings') {
    sendJson(res, 200, settings)
    return
  }
  if (req.method === 'PUT' && path === '/v1/settings') {
    const body = await readBody(req)
    if (settingsPutDelayMs > 0) {
      await new Promise((resolve) => setTimeout(resolve, settingsPutDelayMs))
    }
    const invalid = validateSettingsPut(body)
    if (invalid !== null) return detail(res, 400, invalid)
    settings = { ...settings, ...(body as Partial<EngineSettings>) }
    sendJson(res, 200, settings)
    return
  }
  detail(res, 404, `mock engine: no route for ${req.method} ${path}`)
}

/**
 * Serve `bytes` honoring an inbound `Range` header — 206 + Content-Range for a
 * ranged request, 200 + Accept-Ranges otherwise. Mirrors the engine's
 * FileResponse Range behavior closely enough to exercise media seeking (F5).
 */
function serveBytesWithRange(
  req: IncomingMessage,
  res: ServerResponse,
  bytes: Buffer,
  contentType: string
): void {
  const total = bytes.length
  const range = req.headers.range
  const match = typeof range === 'string' ? /^bytes=(\d*)-(\d*)$/.exec(range.trim()) : null
  if (match !== null) {
    const start = match[1] === '' ? 0 : Number(match[1])
    const end = match[2] === '' ? total - 1 : Math.min(Number(match[2]), total - 1)
    if (Number.isNaN(start) || Number.isNaN(end) || start > end || start >= total) {
      res.writeHead(416, { 'content-range': `bytes */${total}` }).end()
      return
    }
    const chunk = bytes.subarray(start, end + 1)
    res.writeHead(206, {
      'content-type': contentType,
      'content-range': `bytes ${start}-${end}/${total}`,
      'accept-ranges': 'bytes',
      'content-length': String(chunk.length)
    })
    res.end(chunk)
    return
  }
  res.writeHead(200, {
    'content-type': contentType,
    'accept-ranges': 'bytes',
    'content-length': String(total)
  })
  res.end(bytes)
}

function fingerprint(value: string): string {
  // sha256 hex, first 16 chars — mirror of engine/settings.py:token_fingerprint.
  return createHash('sha256').update(value, 'utf8').digest('hex').slice(0, 16)
}

// ---- server ----------------------------------------------------------------------

const server = createServer((req, res) => {
  const path = (req.url ?? '').split('?')[0] ?? ''
  const route = path.startsWith('/__mock/') ? handleControl(req, res, path) : handleV1(req, res, path)
  route.catch((err: unknown) => {
    record('handler-error', String(err))
    if (!res.headersSent) detail(res, 500, String(err))
  })
})

// --spawned: the app OWNS this process (PODCAST_READER_ENGINE_CMD posture),
// rather than adopting a pre-written discovery file. We then mirror the real
// engine's boot: write engine-state.json + engine.json into the data dir, then
// emit the READY sentinel on stdout (the app reads discovery strictly after
// the sentinel, no port polling). Respawn re-runs this command → a fresh PID.
const spawnedMode = process.argv.includes('--spawned')
const READY_SENTINEL = 'PODCAST_READER_READY'

server.listen(0, '127.0.0.1', () => {
  const address = server.address()
  const port = typeof address === 'object' && address !== null ? address.port : 0
  record('listening', String(port))
  if (spawnedMode) {
    const dataDir = process.env.PODCAST_READER_DATA_DIR
    if (dataDir === undefined || dataDir === '') {
      console.error('--spawned requires PODCAST_READER_DATA_DIR')
      process.exit(2)
    }
    writeFileSync(join(dataDir, 'engine-state.json'), JSON.stringify({ port, token }), {
      mode: 0o600
    })
    writeFileSync(
      join(dataDir, 'engine.json'),
      JSON.stringify({
        port,
        pid: process.pid,
        token_fingerprint: fingerprint(token),
        version: '0.3.0'
      }),
      { mode: 0o600 }
    )
    // The sentinel the app's awaitSentinel watches for.
    console.log(READY_SENTINEL)
  }
  console.log(`MOCK_ENGINE_READY ${JSON.stringify({ port, pid: process.pid })}`)
})
