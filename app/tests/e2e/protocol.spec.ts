import { expect, expectEngineState, test } from './fixtures'
import type { Harness } from './fixtures'

/**
 * Protocol confirmation flow (app-shell spec: podcast-reader protocol
 * handling; task 5.2's e2e half): URLs are injected through the
 * `second-instance` seam — the exact production path for Windows protocol
 * launches (`selectProtocolArgv` over the forwarded argv) — and NOTHING
 * protocol-initiated ever executes without an explicit click.
 */

async function deliverProtocolUrl(harness: Harness, url: string): Promise<void> {
  await harness.app.evaluate(({ app }, raw) => {
    // The same emit Electron performs when a second instance launches with
    // the protocol URL in its argv (Windows path; per P8 the matching entry
    // is selected, never popped blindly).
    app.emit('second-instance', {}, ['electron', '--some-chromium-switch', raw], '/', null)
  }, url)
}

async function mockJobs(harness: Harness): Promise<{ id: string; state: string; source: string }[]> {
  return (await (await harness.mock.engine('/v1/jobs')).json()) as {
    id: string
    state: string
    source: string
  }[]
}

test('valid protocol URL lands awaiting confirmation, runs only after Run', async ({
  harness
}) => {
  await expectEngineState(harness.window, 'ready')
  await deliverProtocolUrl(harness, 'podcast-reader://transcribe?url=https://example.com/pod')

  // The app focuses the New view and lists the pending job with its URL.
  await expect
    .poll(async () => harness.window.evaluate(() => window.location.hash))
    .toBe('#/new')
  const confirmCard = harness.window.locator('.confirm-card')
  await expect(confirmCard).toHaveCount(1)
  await expect(confirmCard.locator('.job-source')).toHaveText('https://example.com/pod')

  // Never auto-executes: engine-side it sits in awaiting-confirmation with
  // no pipeline activity, and no confirm call has been made.
  const jobs = await mockJobs(harness)
  expect(jobs).toHaveLength(1)
  expect(jobs[0]?.state).toBe('awaiting-confirmation')
  const log = await harness.mock.log()
  expect(log.some((entry) => entry.detail.includes('/confirm'))).toBe(false)

  await confirmCard.getByRole('button', { name: 'Run' }).click()
  await expect(harness.window.locator('.confirm-card')).toHaveCount(0)
  await expect(harness.window.locator('.job-card .badge')).toHaveText('queued')
  const confirmed = await mockJobs(harness)
  expect(confirmed[0]?.state).toBe('queued')
})

test('Dismiss discards the pending job without executing it', async ({ harness }) => {
  await expectEngineState(harness.window, 'ready')
  await deliverProtocolUrl(harness, 'podcast-reader://transcribe?url=https://example.com/drop')
  const confirmCard = harness.window.locator('.confirm-card')
  await expect(confirmCard).toHaveCount(1)

  await confirmCard.getByRole('button', { name: 'Dismiss' }).click()
  await expect(harness.window.locator('.confirm-card')).toHaveCount(0)
  await expect.poll(async () => (await mockJobs(harness)).length).toBe(0)
  const log = await harness.mock.log()
  expect(log.some((entry) => entry.detail.startsWith('DELETE /v1/jobs/'))).toBe(true)
  expect(log.some((entry) => entry.detail.includes('/confirm'))).toBe(false)
})

test('malformed protocol URLs create nothing', async ({ harness }) => {
  await expectEngineState(harness.window, 'ready')
  await deliverProtocolUrl(harness, 'podcast-reader://wrong-host?url=https://example.com/x')
  await deliverProtocolUrl(harness, 'podcast-reader://transcribe?url=ftp://example.com/x')
  await deliverProtocolUrl(harness, 'podcast-reader://transcribe')
  await deliverProtocolUrl(harness, 'not-a-url-at-all')

  // Give the main process a beat to (not) act, then assert zero jobs ever
  // reached the engine and the New view lists nothing.
  await harness.window.waitForTimeout(750)
  expect(await mockJobs(harness)).toHaveLength(0)
  await harness.window.evaluate(() => {
    window.location.hash = '#/new'
  })
  await expect(harness.window.locator('.confirm-card')).toHaveCount(0)
  const log = await harness.mock.log()
  expect(log.some((entry) => entry.detail === 'POST /v1/jobs')).toBe(false)
})
