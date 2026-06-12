import { expect, storedTrackedJobs, test } from './fixtures'

/**
 * Job-flow e2e (task 8.1, ext-jobs spec): submit the active eligible tab
 * from the popup, watch live progress arrive over the popup-lifetime
 * `/v1/events` stream, and prove the hydrate-then-stream model — a reopened
 * popup renders current job state from records alone, with no events.
 */

test('popup submits the active eligible tab and streams progress to terminal state', async ({
  harness
}) => {
  const popup = await harness.openPopup()
  await harness.seedStorage(popup, { port: harness.mock.port, token: harness.mock.token })

  // Make an eligible page the ACTIVE tab in the popup's window, then
  // re-init the popup: its submit affordance reads the active tab's URL
  // via chrome.tabs.query (per U1 — the popup is the submission surface).
  const siteUrl = `${harness.mock.baseUrl}/episode/42`
  await popup.evaluate(async (url) => {
    await chrome.tabs.create({ url, active: true })
  }, siteUrl)
  await popup.reload()

  await expect(popup.locator('#submit-tab')).toBeVisible()
  // The live stream attaches as part of the connected render; wait for the
  // mock to see it before scripting events so none are dropped.
  await expect
    .poll(async () => (await harness.mock.log()).some((entry) => entry.kind === 'events-open'))
    .toBe(true)

  await popup.click('#submit-tab')
  const row = popup.locator('.job-row')
  await expect(row).toHaveAttribute('data-state', 'queued')
  const jobId = await row.getAttribute('data-job-id')
  expect(jobId).not.toBeNull()

  // No confirmation gate on the token-authed channel (design decision 6):
  // the job landed in `queued`, never `awaiting-confirmation`.
  const records = (await (await harness.mock.engine('/v1/jobs')).json()) as {
    id: string
    source: string
    state: string
  }[]
  expect(records).toHaveLength(1)
  expect(records[0]).toMatchObject({ id: jobId, source: siteUrl, state: 'queued' })

  // Live step progress over the stream (queued → running, step text).
  await harness.mock.control('/job', {
    job: { id: jobId, state: 'queued' },
    events: [
      {
        kind: 'step_started',
        step: 'download',
        message: 'Downloading audio',
        data: { job_id: jobId }
      }
    ]
  })
  await expect(row).toHaveAttribute('data-state', 'running')
  await expect(row.locator('.job-step')).toContainText('download: Downloading audio')

  // Terminal event → the popup re-fetches the record (state-only badge of
  // truth) and renders the terminal state.
  await harness.mock.control('/job', {
    job: { id: jobId, state: 'done' },
    events: [
      { kind: 'job_done', step: null, message: 'Transcript ready', data: { job_id: jobId } }
    ]
  })
  await expect(row).toHaveAttribute('data-state', 'done')
})

test('reopened popup hydrates tracked jobs from records, with no events needed', async ({
  harness
}) => {
  const now = Date.now() / 1000
  await harness.mock.control('/seed', {
    jobs: [
      {
        id: 'job-h',
        source: 'https://example.com/episode',
        title: 'Hydrated Episode',
        state: 'running',
        error: null,
        events: [],
        result: null,
        created_at: now,
        updated_at: now
      }
    ]
  })

  const popup = await harness.openPopup()
  await harness.seedStorage(
    popup,
    { port: harness.mock.port, token: harness.mock.token },
    [
      {
        id: 'job-h',
        source: 'https://example.com/episode',
        title: 'Hydrated Episode',
        submitted_at: now,
        notified: false
      }
    ]
  )
  const row = popup.locator('.job-row[data-job-id="job-h"]')
  await expect(row).toHaveAttribute('data-state', 'running')

  // Progress happens entirely while the popup is CLOSED — no stream alive.
  await popup.close()
  await harness.mock.control('/job', { job: { id: 'job-h', state: 'done' } })

  // Reopen: hydration alone renders the current state (F3 — records are
  // the source of truth; the stream is an optimization).
  const reopened = await harness.openPopup()
  const freshRow = reopened.locator('.job-row[data-job-id="job-h"]')
  await expect(freshRow).toHaveAttribute('data-state', 'done')

  // Seeing the terminal state in the popup acknowledges it: the tracked
  // entry flips to notified, so the SW won't re-notify and the badge clears.
  await expect
    .poll(async () => (await storedTrackedJobs(reopened)).find((j) => j.id === 'job-h')?.notified)
    .toBe(true)
})
