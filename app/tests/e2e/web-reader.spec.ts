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
const SEARCH_CANARY = 's3arch-k4-canary-6f29d17c'
const LEGACY_FONT_IMPORT =
  "@import url('https://fonts.googleapis.com/css2?family=Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,600;0,8..60,700;1,8..60,400&family=JetBrains+Mono:wght@400;600&family=Oswald:wght@400;500;600&display=swap');"

interface CapturedResponse {
  url: string
  requestBody: string
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

test('real HTTPS reader pairs, searches, renders, contains secrets, and rejects framing', async ({}, testInfo) => {
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
          `search_canary = ${JSON.stringify(SEARCH_CANARY)}`,
          "keyless_segments = [{'start': 0.0, 'end': 1.0, 'text': 'The team compares local retrieval architecture.'}, {'start': 1.0, 'end': 2.0, 'text': 'Privacy boundaries keep every transcript on the engine.'}, {'start': 2.0, 'end': 3.0, 'text': f'Private test marker {search_canary}.'}]",
          "chapter_segments = [{'start': 0.0, 'end': 2.0, 'text': 'A field guide to durable note taking and review habits.'}]",
          "legacy_segments = [{'start': 0.0, 'end': 2.0, 'text': 'Sourdough timing depends on temperature and starter strength.'}]",
          "chapters = [{'start': 0.0, 'end': 2.0, 'title': 'Opening', 'abstract': 'A chapter.', 'type': 'content', 'key_points': []}]",
          "for source_id, title, chapter_data, segments in [(ids[0], 'Keyless episode', None, keyless_segments), (ids[1], 'Chaptered episode', chapters, chapter_segments)]:",
          "    path = library / source_id / f'{source_id}.html'",
          '    path.parent.mkdir(parents=True, exist_ok=True)',
          '    path.write_text(build_html(segments, title, chapters=chapter_data, sentences_per_para=1), encoding="utf8")',
          "    add_entry(library, LibraryEntry(source_id=source_id, source='https://example.com/private?secret=source', title=title, html_path=str(path), created_at=time.time()))",
          "source_id, title = ids[2], 'Legacy cached episode'",
          "path = library / source_id / f'{source_id}.html'",
          'path.parent.mkdir(parents=True, exist_ok=True)',
          `legacy_import = ${JSON.stringify(LEGACY_FONT_IMPORT)}`,
          "document = build_html(legacy_segments, title).replace('<style>\\n', f'<style>\\n{legacy_import}\\n\\n', 1)",
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
        const requestChunks: Buffer[] = []
        incoming.on('data', (chunk: Buffer) => requestChunks.push(chunk))
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
                requestBody: Buffer.concat(requestChunks).toString(),
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
    const searchRequests: Array<{ body: string; headers: Record<string, string> }> = []
    const artifactFontRequests: string[] = []
    const artifactThirdPartyRequests: string[] = []
    context.on('request', (request) => {
      if (request.url().startsWith(webOrigin)) requestUrls.push(request.url())
      if (new URL(request.url()).pathname === '/web/api/search') {
        searchRequests.push({ body: request.postData() ?? '', headers: request.headers() })
      }
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

    const search = page.getByLabel('Search transcripts')
    expect(await search.getAttribute('type')).toBe('search')
    expect(await search.getAttribute('name')).toBeNull()
    expect(await search.getAttribute('autocomplete')).toBe('off')
    expect(await search.getAttribute('spellcheck')).toBe('false')
    expect(await search.getAttribute('autocorrect')).toBe('off')
    expect(await search.getAttribute('autocapitalize')).toBe('none')
    expect(await search.getAttribute('inputmode')).toBe('search')
    expect(await search.evaluate((node) => document.activeElement === node)).toBe(false)

    let staleStarted = false
    let busyAttempts = 0
    await page.route('**/web/api/search', async (route) => {
      const body = route.request().postDataJSON() as { query: string }
      if (body.query === 'durable habits') {
        staleStarted = true
        await new Promise((resolve) => setTimeout(resolve, 700))
        await route
          .fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({
              results: [{ source_id: 'd'.repeat(64), title: 'Stale result', excerpt: 'Old query' }],
              has_more: false,
              partial: false
            })
          })
          .catch(() => {})
      } else if (body.query === 'retrieval privacy') {
        busyAttempts += 1
        if (busyAttempts === 1) {
          await route.fulfill({
            status: 429,
            headers: { 'Content-Type': 'application/json', 'Retry-After': '1' },
            body: '{"detail":"search busy"}'
          })
        } else {
          await route.continue()
        }
      } else {
        await route.continue()
      }
    })
    await page.emulateMedia({ colorScheme: 'light' })
    await search.fill('durable habits')
    await expect.poll(() => staleStarted).toBe(true)
    await search.fill('retrieval privacy')
    await expect(page.getByRole('status')).toHaveText('Searching…')
    await expect(page.getByText('The team compares local retrieval architecture.')).toBeVisible()
    await expect(page.getByRole('status')).toHaveText('1 match.')
    await expect(page.getByText('Stale result')).toHaveCount(0)
    expect(busyAttempts).toBe(2)
    await page.unroute('**/web/api/search')
    const phoneSearch = testInfo.outputPath('phone-search-390-light.png')
    await page.screenshot({ path: phoneSearch, fullPage: true })
    await testInfo.attach('phone-search-390-light.png', {
      path: phoneSearch,
      contentType: 'image/png'
    })

    await page.getByRole('button', { name: /Keyless episode/ }).click()
    const searchedFrame = page.frameLocator('iframe')
    await expect(searchedFrame.getByText('The team compares local retrieval architecture.')).toBeVisible()
    await page.getByRole('button', { name: '← Library' }).click()
    await expect(page.getByRole('heading', { name: 'Your library' })).toBeVisible()
    expect(await page.getByLabel('Search transcripts').evaluate((node) => document.activeElement === node)).toBe(false)
    await expect(page.getByLabel('Search transcripts')).toHaveValue('')

    await page.getByLabel('Search transcripts').fill('x')
    await expect(page.getByRole('status')).toHaveText('Enter at least 2 characters.')
    await expect(page.getByRole('button', { name: 'Keyless episode' })).toBeVisible()
    await page.getByLabel('Search transcripts').fill('no-such-private-transcript')
    await expect(page.getByRole('status')).toHaveText('0 matches.')
    await expect(page.getByText('No transcript matches.', { exact: true })).toHaveCount(1)
    await page.getByRole('button', { name: 'Clear' }).click()

    await page.route('**/web/api/search', (route) =>
      route.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"failure"}' })
    )
    await page.getByLabel('Search transcripts').fill('sourdough temperature')
    await expect(page.getByRole('button', { name: 'Retry' })).toBeVisible()
    await page.unroute('**/web/api/search')
    await page.getByRole('button', { name: 'Retry' }).click()
    await expect(page.getByText('Sourdough timing depends on temperature')).toBeVisible()
    await page.getByRole('button', { name: 'Clear' }).click()

    await page.getByLabel('Search transcripts').fill(SEARCH_CANARY)
    await expect(page.getByText(`Private test marker ${SEARCH_CANARY}.`)).toBeVisible()
    await expect(page.getByRole('status')).toHaveText('1 match.')
    const liveCanaryAttributes = await page.evaluate(() =>
      Array.from(document.querySelectorAll('*')).flatMap((element) =>
        element.getAttributeNames().map((name) => `${name}=${element.getAttribute(name) ?? ''}`)
      )
    )
    expect(JSON.stringify(liveCanaryAttributes)).not.toContain(SEARCH_CANARY)
    await page.getByRole('button', { name: 'Clear' }).click()
    await expect(page.getByLabel('Search transcripts')).toHaveValue('')
    expect(await page.locator('body').textContent()).not.toContain(SEARCH_CANARY)
    const clearedCanaryAttributes = await page.evaluate(() =>
      Array.from(document.querySelectorAll('*')).flatMap((element) =>
        element.getAttributeNames().map((name) => `${name}=${element.getAttribute(name) ?? ''}`)
      )
    )
    expect(JSON.stringify(clearedCanaryAttributes)).not.toContain(SEARCH_CANARY)

    let expiryAttempts = 0
    await page.route('**/web/api/search', async (route) => {
      expiryAttempts += 1
      await new Promise((resolve) => setTimeout(resolve, 2900))
      await route.fulfill({
        status: 429,
        headers: { 'Content-Type': 'application/json', 'Retry-After': '1' },
        body: '{"detail":"search busy"}'
      })
    })
    await page.getByLabel('Search transcripts').fill('busy forever')
    await expect(page.getByRole('button', { name: 'Retry' })).toBeVisible({ timeout: 6000 })
    expect(expiryAttempts).toBe(1)
    await page.unroute('**/web/api/search')
    await page.getByRole('button', { name: 'Clear' }).click()

    for (const [title, text] of [
      ['Chaptered episode', 'A field guide to durable note taking and review habits.'],
      ['Legacy cached episode', 'Sourdough timing depends on temperature and starter strength.']
    ] as const) {
      await page.getByRole('button', { name: title }).click()
      const frame = page.frameLocator('iframe')
      await expect(frame.getByText(text)).toBeVisible()
      await page.getByRole('button', { name: '← Library' }).click()
    }
    expect(consoleText.filter((message) => message.startsWith('CSP-VIOLATION:'))).toEqual([])

    const legacyViolations = consoleText.filter((message) => message.startsWith('CSP-VIOLATION:'))
    expect(legacyViolations).toEqual([])
    expect(artifactFontRequests).toEqual([])
    expect(artifactThirdPartyRequests).toEqual([])
    await page.setViewportSize({ width: 1280, height: 900 })
    await page.emulateMedia({ colorScheme: 'dark' })
    await page.reload()
    await expect(page.getByRole('heading', { name: 'Your library' })).toBeVisible()
    await page.getByLabel('Search transcripts').fill('retrieval privacy')
    await expect(page.getByText('The team compares local retrieval architecture.')).toBeVisible()
    await expect(page.getByRole('status')).toHaveText('1 match.')
    const desktopSearch = testInfo.outputPath('desktop-search-1280-dark.png')
    await page.screenshot({ path: desktopSearch, fullPage: true })
    await testInfo.attach('desktop-search-1280-dark.png', {
      path: desktopSearch,
      contentType: 'image/png'
    })
    await page.getByRole('button', { name: 'Clear' }).click()

    await page.route('**/web/api/search', (route) =>
      route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: '{"detail":"unauthorized"}'
      })
    )
    await page.getByLabel('Search transcripts').fill(SEARCH_CANARY)
    await expect(page.getByRole('heading', { name: 'Connect this browser' })).toBeVisible()
    await expect(page.getByLabel('Search transcripts')).toHaveCount(0)
    expect(await page.locator('body').textContent()).not.toContain(SEARCH_CANARY)
    const expiredCanaryAttributes = await page.evaluate(() =>
      Array.from(document.querySelectorAll('*')).flatMap((element) =>
        element.getAttributeNames().map((name) => `${name}=${element.getAttribute(name) ?? ''}`)
      )
    )
    expect(JSON.stringify(expiredCanaryAttributes)).not.toContain(SEARCH_CANARY)
    await page.unroute('**/web/api/search')
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
      caches: (await caches.keys()).join(','),
      formState: Array.from(document.querySelectorAll('input')).map((input) => ({
        name: input.name,
        value: input.value,
        autocomplete: input.autocomplete
      }))
    }))
    const forbidden = [state.token, webSession, 'prws1.']
    for (const secret of forbidden) {
      expect(JSON.stringify(browserStorage)).not.toContain(secret)
      expect(consoleText.join('\n')).not.toContain(secret)
    }
    expect(JSON.stringify(browserStorage)).not.toContain(SEARCH_CANARY)
    expect(consoleText.join('\n')).not.toContain(SEARCH_CANARY)
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
      expect(url).not.toContain(SEARCH_CANARY)
    }
    expect(searchRequests.some((request) => request.body.includes(SEARCH_CANARY))).toBe(true)
    for (const request of searchRequests) {
      expect(JSON.stringify(request.headers)).not.toContain(SEARCH_CANARY)
    }
    for (const response of proxyResponses.filter((item) => item.url.startsWith(webOrigin))) {
      const pathname = new URL(response.url).pathname
      const isClaim = pathname === '/web/api/pair/claim'
      const isSession = pathname === '/web/api/session'
      const canContainSearchCanary =
        (pathname === '/web/api/search' && response.requestBody.includes(SEARCH_CANARY)) ||
        pathname === `/web/api/transcripts/${SOURCE_IDS[0]}.html`
      if (!canContainSearchCanary) expect(response.body).not.toContain(SEARCH_CANARY)
      if (pathname === '/web/api/search' && !response.requestBody.includes(SEARCH_CANARY)) {
        expect(response.requestBody).not.toContain(SEARCH_CANARY)
      }
      expect(JSON.stringify(response.headers)).not.toContain(SEARCH_CANARY)
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
    expect(engineLogs.join('\n')).not.toContain(SEARCH_CANARY)
    await rm(dataDir, { recursive: true, force: true })
    await rm(tlsDir, { recursive: true, force: true })
  }
})
