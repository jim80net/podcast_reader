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
import { appendFileSync } from 'node:fs'
import { createServer } from 'node:http'
import type { IncomingMessage, ServerResponse } from 'node:http'

// ---- mirrored payload shapes (kept structurally equal to src/shared/types.ts) --

interface JobError {
  code: string
  message: string
  hint: string
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
  custom_provider_url: ''
}
let keyTestResult: { ok: boolean; detail: string | null } = { ok: true, detail: null }
const pushedKeys = new Set<string>()
let jobCounter = 0
const sseClients = new Set<ServerResponse>()

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

function makeJob(source: string, title: string | null, state: string): JobRecord {
  jobCounter += 1
  const job: JobRecord = {
    id: `mock-job-${jobCounter}`,
    source,
    title,
    state,
    error: null,
    events: [],
    result: null,
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
  const provider = (body.chapter_provider as string | undefined) ?? settings.chapter_provider
  if (!PROVIDERS.includes(provider)) return `unknown chapter provider: '${provider}'`
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
    res.writeHead(204).end()
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
  detail(res, 404, `unknown mock control endpoint: ${path}`)
}

// ---- /v1 surface ----------------------------------------------------------------

async function handleV1(req: IncomingMessage, res: ServerResponse, path: string): Promise<void> {
  const auth = req.headers.authorization ?? ''
  if (auth !== `Bearer ${token}`) {
    record('unauthorized', `${req.method} ${path}`)
    sendJson(res, 401, { detail: 'unauthorized' })
    return
  }
  record('request', `${req.method} ${path}`)

  if (req.method === 'GET' && path === '/v1/health') {
    sendJson(res, 200, { version: '0.1.0', token_fingerprint: fingerprint(token) })
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
      body.requires_confirmation === true ? 'awaiting-confirmation' : 'queued'
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
  if (req.method === 'GET' && path === '/v1/library') {
    sendJson(res, 200, library)
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
    if (!PROVIDERS.includes(provider)) {
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
    if (!PROVIDERS.includes(body.provider as string)) {
      return detail(res, 400, `unknown chapter provider: '${String(body.provider)}'`)
    }
    record('keys-test', body.provider as string)
    sendJson(res, 200, keyTestResult)
    return
  }
  if (req.method === 'GET' && path === '/v1/providers') {
    sendJson(
      res,
      200,
      PROVIDERS.map((id) => ({
        id,
        default_model: id === 'custom' ? '' : `${id}-default-model`,
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
    const invalid = validateSettingsPut(body)
    if (invalid !== null) return detail(res, 400, invalid)
    settings = { ...settings, ...(body as Partial<EngineSettings>) }
    sendJson(res, 200, settings)
    return
  }
  detail(res, 404, `mock engine: no route for ${req.method} ${path}`)
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

server.listen(0, '127.0.0.1', () => {
  const address = server.address()
  const port = typeof address === 'object' && address !== null ? address.port : 0
  record('listening', String(port))
  console.log(`MOCK_ENGINE_READY ${JSON.stringify({ port, pid: process.pid })}`)
})
