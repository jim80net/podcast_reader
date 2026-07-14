import { spawn, spawnSync } from 'node:child_process'
import { request as httpRequest } from 'node:http'
import { createServer as createHttpsServer } from 'node:https'
import { existsSync } from 'node:fs'
import { mkdtemp, readFile, readdir, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

import { chromium, expect, test } from '@playwright/test'

const REPO_ROOT = resolve(__dirname, '../../..')
const SOURCE_IDS = ['a'.repeat(64), 'b'.repeat(64), 'c'.repeat(64)] as const
const LEGACY_FONT_IMPORT =
  "@import url('https://fonts.googleapis.com/css2?family=Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,600;0,8..60,700;1,8..60,400&family=JetBrains+Mono:wght@400;600&family=Oswald:wght@400;500;600&display=swap');"

interface CapturedResponse {
  url: string
  body: string
  headers: Record<string, string>
}

interface FramedTranscriptResponse {
  status: number
  csp: string
  cookie: string
}

async function waitForFile(path: string): Promise<void> {
  await expect.poll(() => existsSync(path), { timeout: 15_000 }).toBe(true)
}

async function filesBelow(root: string): Promise<string[]> {
  const result: string[] = []
  for (const item of await readdir(root, { withFileTypes: true })) {
    const path = join(root, item.name)
    if (item.isDirectory()) result.push(...(await filesBelow(path)))
    else if (item.isFile()) result.push(path)
  }
  return result
}

test('real HTTPS reader pairs, renders, reloads, contains secrets, and rejects framing', async () => {
  test.setTimeout(180_000)
  const dataDir = await mkdtemp(join(tmpdir(), 'pr-web-data-'))
  const tlsDir = await mkdtemp(join(tmpdir(), 'pr-web-tls-'))
  const cert = join(tlsDir, 'cert.pem')
  const key = join(tlsDir, 'key.pem')
  const generated = spawnSync(
    'openssl',
    [
      'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', key, '-out', cert,
      '-days', '1', '-subj', '/CN=web.test',
      '-addext', 'subjectAltName=DNS:web.test,DNS:evil.web.test,DNS:evil.test'
    ],
    { encoding: 'utf8' }
  )
  expect(generated.status, generated.stderr).toBe(0)

  const engineLogs: string[] = []
  const engine = spawn('uv', ['run', 'podcast-reader', 'serve'], {
    cwd: REPO_ROOT,
    env: { ...process.env, PODCAST_READER_DATA_DIR: dataDir },
    stdio: ['ignore', 'pipe', 'pipe']
  })
  engine.stdout.on('data', (chunk: Buffer) => engineLogs.push(chunk.toString()))
  engine.stderr.on('data', (chunk: Buffer) => engineLogs.push(chunk.toString()))

  let proxy: ReturnType<typeof createHttpsServer> | undefined
  let browser: Awaited<ReturnType<typeof chromium.launch>> | undefined
  let engineBearer = ''
  let webSession = ''
  const foreignClaimStatuses: number[] = []
  const framedTranscriptResponses: FramedTranscriptResponse[] = []
  const proxyResponses: CapturedResponse[] = []
  try {
    await waitForFile(join(dataDir, 'engine.json'))
    const discovery = JSON.parse(await readFile(join(dataDir, 'engine.json'), 'utf8')) as {
      port: number
    }
    const statePath = join(dataDir, 'engine-state.json')
    const state = JSON.parse(await readFile(statePath, 'utf8')) as { token: string }
    engineBearer = state.token

    const seeded = spawnSync(
      'uv',
      [
        'run', 'python', '-c',
        [
          'import sys, time',
          'from pathlib import Path',
          'from podcast_reader.engine.library import add_entry',
          'from podcast_reader.html import build_html',
          'from podcast_reader.types import LibraryEntry',
          'library = Path(sys.argv[1])',
          `ids = ${JSON.stringify(SOURCE_IDS)}`,
          "segments = [{'start': 0.0, 'end': 2.0, 'text': 'Browser-only marker sentence.'}]",
          "chapters = [{'start': 0.0, 'end': 2.0, 'title': 'Opening', 'abstract': 'A chapter.', 'type': 'content', 'key_points': []}]",
          "for source_id, title, chapter_data in [(ids[0], 'Keyless episode', None), (ids[1], 'Chaptered episode', chapters)]:",
          "    path = library / source_id / f'{source_id}.html'",
          '    path.parent.mkdir(parents=True, exist_ok=True)',
          '    path.write_text(build_html(segments, title, chapters=chapter_data), encoding="utf8")',
          "    add_entry(library, LibraryEntry(source_id=source_id, source='https://example.com/private?secret=source', title=title, html_path=str(path), created_at=time.time()))",
          "source_id, title = ids[2], 'Legacy cached episode'",
          "path = library / source_id / f'{source_id}.html'",
          'path.parent.mkdir(parents=True, exist_ok=True)',
          `legacy_import = ${JSON.stringify(LEGACY_FONT_IMPORT)}`,
          "document = build_html(segments, title).replace('<style>\\n', f'<style>\\n{legacy_import}\\n\\n', 1)",
          'path.write_text(document, encoding="utf8")',
          "add_entry(library, LibraryEntry(source_id=source_id, source='https://example.com/legacy', title=title, html_path=str(path), created_at=time.time()))"
        ].join('\n'),
        join(dataDir, 'library')
      ],
      { cwd: REPO_ROOT, encoding: 'utf8' }
    )
    expect(seeded.status, seeded.stderr).toBe(0)

    proxy = createHttpsServer(
      { cert: await readFile(cert), key: await readFile(key) },
      (incoming, outgoing) => {
        if (incoming.headers.host?.startsWith('evil.web.test') && incoming.url === '/attack') {
          outgoing.writeHead(200, { 'content-type': 'text/html' })
          outgoing.end(
            `<iframe src="https://web.test:${(proxy?.address() as { port: number }).port}/web/api/transcripts/${SOURCE_IDS[0]}.html"></iframe>`
          )
          return
        }
        if (incoming.headers.host?.startsWith('evil.test') && incoming.url === '/attack') {
          outgoing.writeHead(200, { 'content-type': 'text/html' })
          outgoing.end('<!doctype html><title>Foreign origin</title>')
          return
        }
        const forwarded = httpRequest(
          {
            hostname: '127.0.0.1',
            port: discovery.port,
            path: incoming.url,
            method: incoming.method,
            headers: incoming.headers
          },
          (response) => {
            if (
              incoming.url === '/web/api/pair/claim' &&
              incoming.headers.origin?.startsWith('https://evil.test:')
            ) {
              foreignClaimStatuses.push(response.statusCode ?? 500)
            }
            if (
              incoming.url === `/web/api/transcripts/${SOURCE_IDS[0]}.html` &&
              incoming.headers.referer?.startsWith('https://evil.web.test:')
            ) {
              framedTranscriptResponses.push({
                status: response.statusCode ?? 500,
                csp: String(response.headers['content-security-policy'] ?? ''),
                cookie: String(incoming.headers.cookie ?? '')
              })
            }
            const chunks: Buffer[] = []
            response.on('data', (chunk: Buffer) => chunks.push(chunk))
            response.on('end', () => {
              const body = Buffer.concat(chunks)
              proxyResponses.push({
                url: `https://${incoming.headers.host ?? ''}${incoming.url ?? ''}`,
                body: body.toString(),
                headers: Object.fromEntries(
                  Object.entries(response.headers).map(([name, value]) => [name, String(value ?? '')])
                )
              })
              outgoing.writeHead(response.statusCode ?? 500, response.headers)
              outgoing.end(body)
            })
          }
        )
        forwarded.on('error', () => {
          outgoing.writeHead(502).end()
        })
        incoming.pipe(forwarded)
      }
    )
    await new Promise<void>((resolveListen) => proxy?.listen(0, '127.0.0.1', resolveListen))
    const proxyPort = (proxy.address() as { port: number }).port
    const webOrigin = `https://web.test:${proxyPort}`

    const mint = await fetch(`http://127.0.0.1:${discovery.port}/v1/pair`, {
      method: 'POST',
      headers: { authorization: `Bearer ${state.token}` }
    })
    expect(mint.status).toBe(200)
    const { code } = (await mint.json()) as { code: string }

    // Direct loopback HTTP cannot impersonate the external HTTPS authority,
    // and a rejected gate must not consume the one-time code.
    const loopbackClaim = await fetch(
      `http://127.0.0.1:${discovery.port}/web/api/pair/claim`,
      {
        method: 'POST',
        headers: {
          'content-type': 'application/json',
          origin: webOrigin,
          'sec-fetch-site': 'same-origin'
        },
        body: JSON.stringify({ code })
      }
    )
    expect(loopbackClaim.status).toBe(403)

    browser = await chromium.launch({
      args: ['--host-resolver-rules=MAP web.test 127.0.0.1,MAP evil.web.test 127.0.0.1,MAP evil.test 127.0.0.1']
    })
    const context = await browser.newContext({
      ignoreHTTPSErrors: true,
      viewport: { width: 390, height: 844 }
    })
    const requestUrls: string[] = []
    const artifactFontRequests: string[] = []
    const artifactThirdPartyRequests: string[] = []
    context.on('request', (request) => {
      if (request.url().startsWith(webOrigin)) requestUrls.push(request.url())
      const frameUrl = request.frame().url()
      const fromArtifact = frameUrl.includes('/web/api/transcripts/')
      if (fromArtifact && request.resourceType() === 'font') artifactFontRequests.push(request.url())
      if (
        fromArtifact &&
        /^https?:/.test(request.url()) &&
        !request.url().startsWith(webOrigin)
      ) {
        artifactThirdPartyRequests.push(request.url())
      }
    })

    const foreign = await context.newPage()
    await foreign.goto(`https://evil.test:${proxyPort}/attack`)
    await foreign.evaluate(
      async ({ origin, pairingCode }) => {
        try {
          await fetch(`${origin}/web/api/pair/claim`, {
            method: 'POST',
            headers: { 'Content-Type': 'text/plain' },
            body: JSON.stringify({ code: pairingCode })
          })
        } catch {
          // The missing CORS grant is expected; the proxy records the engine status.
        }
      },
      { origin: webOrigin, pairingCode: code }
    )
    await expect.poll(() => foreignClaimStatuses).toContain(403)
    await foreign.close()

    const directHttp = await context.newPage()
    await directHttp.goto(`http://127.0.0.1:${discovery.port}/web/`)
    await expect(directHttp.getByRole('heading', { name: 'Connect this browser' })).toBeVisible()
    await directHttp.getByLabel('Pairing code').fill(code)
    await directHttp.getByRole('button', { name: 'Connect' }).click()
    await expect(
      directHttp.getByText('That code could not be verified. Create a new code and try again.')
    ).toBeVisible()
    expect(await context.cookies(`http://127.0.0.1:${discovery.port}/web/`)).toHaveLength(0)
    await directHttp.close()

    const page = await context.newPage()
    const consoleText: string[] = []
    page.on('console', (message) => consoleText.push(message.text()))
    await page.addInitScript(() => {
      window.addEventListener('securitypolicyviolation', (event) => {
        console.error(`CSP-VIOLATION:${event.violatedDirective}:${event.blockedURI}`)
      })
    })

    await page.goto(`${webOrigin}/web/`)
    await expect(page.getByRole('heading', { name: 'Connect this browser' })).toBeVisible()
    await page.getByLabel('Pairing code').fill(code)
    await page.getByRole('button', { name: 'Connect' }).click()
    await expect(page.getByRole('heading', { name: 'Your library' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Keyless episode' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Chaptered episode' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Legacy cached episode' })).toBeVisible()

    for (const title of ['Keyless episode', 'Chaptered episode']) {
      await page.getByRole('button', { name: title }).click()
      const frame = page.frameLocator('iframe')
      await expect(frame.getByText('Browser-only marker sentence.')).toBeVisible()
      await page.getByRole('button', { name: '← Library' }).click()
    }
    expect(consoleText.filter((message) => message.startsWith('CSP-VIOLATION:'))).toEqual([])

    await page.getByRole('button', { name: 'Legacy cached episode' }).click()
    const legacyFrame = page.frameLocator('iframe')
    await expect(legacyFrame.getByText('Browser-only marker sentence.')).toBeVisible()
    await page.getByRole('button', { name: '← Library' }).click()
    const legacyViolations = consoleText.filter((message) => message.startsWith('CSP-VIOLATION:'))
    expect(legacyViolations).toEqual([])
    expect(artifactFontRequests).toEqual([])
    expect(artifactThirdPartyRequests).toEqual([])
    await page.setViewportSize({ width: 1280, height: 900 })
    await page.reload()
    await expect(page.getByRole('heading', { name: 'Your library' })).toBeVisible()

    const cookies = await context.cookies(`${webOrigin}/web/`)
    expect(cookies).toHaveLength(1)
    const session = cookies[0]
    expect(session?.name).toBe('__Secure-podcast_reader_web')
    expect(session?.httpOnly).toBe(true)
    expect(session?.secure).toBe(true)
    expect(session?.sameSite).toBe('Strict')
    expect(session?.path).toBe('/web/')
    webSession = session?.value ?? ''
    expect(webSession).toMatch(/^prws1\./)
    expect(await page.evaluate(() => document.cookie)).toBe('')
    const browserStorage = await page.evaluate(async () => ({
      body: document.body.textContent ?? '',
      url: location.href,
      local: JSON.stringify(localStorage),
      session: JSON.stringify(sessionStorage),
      databases: (await indexedDB.databases()).map((item) => item.name ?? '').join(','),
      caches: (await caches.keys()).join(',')
    }))
    const forbidden = [state.token, webSession, 'prws1.']
    for (const secret of forbidden) {
      expect(JSON.stringify(browserStorage)).not.toContain(secret)
      expect(consoleText.join('\n')).not.toContain(secret)
    }
    expect(consoleText.filter((message) => message.startsWith('CSP-VIOLATION:'))).toEqual([])

    const attack = await context.newPage()
    await attack.goto(`https://evil.web.test:${proxyPort}/attack`)
    await expect(attack.locator('iframe')).toBeVisible()
    await expect.poll(() => framedTranscriptResponses).toHaveLength(1)
    expect(framedTranscriptResponses[0]).toMatchObject({ status: 200 })
    expect(framedTranscriptResponses[0]?.cookie).toContain(
      `__Secure-podcast_reader_web=${webSession}`
    )
    expect(framedTranscriptResponses[0]?.csp).toContain("frame-ancestors 'self'")
    await expect.poll(() => attack.frames().some((frame) => frame.url().includes(SOURCE_IDS[0]))).toBe(false)

    const files = await filesBelow(dataDir)
    const containingToken: string[] = []
    for (const path of files) {
      if ((await readFile(path)).toString().includes(state.token)) containingToken.push(path)
      expect((await readFile(path)).toString()).not.toContain('prws1.')
    }
    expect(containingToken).toEqual([statePath])

    await page.bringToFront()
    await page.getByRole('button', { name: 'Log out' }).click()
    await expect(page.getByRole('heading', { name: 'Connect this browser' })).toBeVisible()
    expect(await context.cookies(`${webOrigin}/web/`)).toHaveLength(0)

    for (const url of requestUrls) {
      for (const secret of forbidden) expect(url).not.toContain(secret)
    }
    for (const response of proxyResponses.filter((item) => item.url.startsWith(webOrigin))) {
      const isClaim = new URL(response.url).pathname === '/web/api/pair/claim'
      const isSession = new URL(response.url).pathname === '/web/api/session'
      for (const secret of forbidden) {
        if (!isClaim) expect(response.body).not.toContain(secret)
        const headers = { ...response.headers }
        if (isSession) delete headers['set-cookie']
        expect(JSON.stringify(headers)).not.toContain(secret)
      }
    }
  } finally {
    await browser?.close()
    proxy?.closeAllConnections()
    proxy?.close()
    if (engine.exitCode === null) engine.kill('SIGTERM')
    await Promise.race([
      new Promise<void>((resolveExit) => engine.once('exit', () => resolveExit())),
      new Promise<void>((resolveTimeout) => setTimeout(resolveTimeout, 5_000))
    ])
    if (engine.exitCode === null) engine.kill('SIGKILL')
    if (engineBearer) expect(engineLogs.join('\n')).not.toContain(engineBearer)
    if (webSession) expect(engineLogs.join('\n')).not.toContain(webSession)
    expect(engineLogs.join('\n')).not.toContain('prws1.')
    await rm(dataDir, { recursive: true, force: true })
    await rm(tlsDir, { recursive: true, force: true })
  }
})
