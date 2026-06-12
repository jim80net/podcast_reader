import { expect, test } from './fixtures'

/**
 * Cookie-capture e2e (task 8.1, ext-cookie-capture spec): the full push
 * path against the mock — failed `download_auth_required` job → capture
 * affordance naming the registrable domain → permission request scoped to
 * that domain → Netscape serialization → authed `PUT /v1/cookies` (the
 * mock runs the real validation) → one-click resubmission.
 *
 * Chrome renders the optional-permission prompt outside any automatable
 * page (design decision 12), so the grant is PRE-GRANTED here by stubbing
 * `chrome.permissions.request` (recording its arguments — the scoping
 * assertion survives) and `chrome.cookies.getAll` (unavailable without a
 * real grant) with fixture cookies before the popup loads. The prompt
 * itself is a documented manual check (extension/README.md); the
 * grant/decline branching is unit-tested (src/capture.test.ts).
 */

/** chrome.cookies.Cookie fixtures: a parent-domain httpOnly login cookie and a subdomain session cookie. */
const FIXTURE_COOKIES = [
  {
    domain: '.example.com',
    path: '/',
    secure: true,
    httpOnly: true,
    session: false,
    expirationDate: 1_900_000_000.123,
    name: 'session',
    value: 'tok-abc'
  },
  {
    domain: 'media.example.com',
    path: '/',
    secure: true,
    httpOnly: false,
    session: true,
    name: 'player',
    value: 'v1'
  }
]

const EXPECTED_JAR =
  '# Netscape HTTP Cookie File\n' +
  '#HttpOnly_.example.com\tTRUE\t/\tTRUE\t1900000000\tsession\ttok-abc\n' +
  'media.example.com\tFALSE\t/\tTRUE\t0\tplayer\tv1\n'

test('capture pushes a domain-scoped Netscape jar and offers resubmission', async ({
  harness
}) => {
  const now = Date.now() / 1000
  await harness.mock.control('/seed', {
    jobs: [
      {
        id: 'job-auth',
        source: 'https://media.example.com/clip',
        title: null,
        state: 'failed',
        error: {
          code: 'download_auth_required',
          message: 'This source requires authentication to download.',
          hint: 'Share your login via the browser extension, or import a cookies file in Settings.'
        },
        events: [],
        result: null,
        created_at: now,
        updated_at: now
      }
    ]
  })

  const popup = await harness.context.newPage()
  await popup.addInitScript((fixtures) => {
    const requests: unknown[] = []
    ;(window as unknown as Record<string, unknown>).__permissionRequests = requests
    Object.defineProperty(chrome.permissions, 'request', {
      value: async (req: unknown) => {
        requests.push(req)
        return true
      }
    })
    Object.defineProperty(chrome, 'cookies', {
      value: { getAll: async () => fixtures }
    })
  }, FIXTURE_COOKIES)
  await popup.goto(`chrome-extension://${harness.extensionId}/popup.html`)
  await harness.seedStorage(
    popup,
    { port: harness.mock.port, token: harness.mock.token },
    [
      {
        id: 'job-auth',
        source: 'https://media.example.com/clip',
        title: null,
        submitted_at: now,
        notified: true
      }
    ]
  )

  // The affordance exists only on download_auth_required failures and names
  // the REGISTRABLE domain of the subdomain source (per U4).
  const row = popup.locator('.job-row[data-job-id="job-auth"]')
  await expect(row).toHaveAttribute('data-state', 'failed')
  const capture = popup.locator('#capture-job-auth')
  await expect(capture).toHaveText('Share your example.com login')

  await capture.click()
  await expect(row.locator('[role="status"]')).toHaveText('Login shared.')

  // The permission request was scoped to the registrable domain only.
  expect(
    await popup.evaluate(
      () => (window as unknown as Record<string, unknown>).__permissionRequests
    )
  ).toEqual([
    {
      permissions: ['cookies'],
      origins: ['https://example.com/*', 'https://*.example.com/*']
    }
  ])

  // The jar the engine received (via the test-only /__mock/cookies seam):
  // declared under the registrable domain, exact Netscape shape — header,
  // 7 tab-separated fields, #HttpOnly_ prefix, dot-domain subdomain flag.
  const jars = (await (await harness.mock.control('/cookies')).json()) as {
    domain: string
    jar: string
  }[]
  expect(jars).toHaveLength(1)
  expect(jars[0]).toMatchObject({ domain: 'example.com', jar: EXPECTED_JAR })

  // Retain nothing: no cookie value anywhere in extension storage.
  const storageDump = await popup.evaluate(async () =>
    JSON.stringify(await chrome.storage.local.get(null))
  )
  expect(storageDump).not.toContain('tok-abc')

  // One-click resubmission of the failed source, queued (no confirm gate).
  await popup.click('#retry-job-auth')
  await expect
    .poll(async () => {
      const records = (await (await harness.mock.engine('/v1/jobs')).json()) as {
        source: string
        state: string
      }[]
      return records.filter(
        (record) => record.source === 'https://media.example.com/clip' && record.state === 'queued'
      ).length
    })
    .toBe(1)
})
