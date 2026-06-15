import { expect, expectEngineState, test } from './fixtures'
import type { Harness } from './fixtures'

/**
 * Four-view happy paths against the mock engine (app-views spec, task 7.2):
 * Library cards + Reader handoff, New submission with live step progress and
 * structured failure display, Settings round-trip with engine-side key test
 * and inline validation errors, and SSE-drop → hydration recovery.
 */

async function seedLibrary(harness: Harness): Promise<void> {
  await harness.mock.control('/seed', {
    library: [
      {
        source_id: 'ep-one',
        source: 'https://example.com/episodes/1',
        title: 'First Episode',
        html_path: '/mock/ep-one.html',
        created_at: 1_700_000_000
      },
      {
        source_id: 'ep-two',
        source: 'https://example.com/episodes/2',
        title: 'Second Episode',
        html_path: '/mock/ep-two.html',
        created_at: 1_700_100_000
      }
    ],
    transcripts: {
      'ep-one': '<!DOCTYPE html><html><body><p>episode one transcript</p></body></html>',
      'ep-two': '<!DOCTYPE html><html><body><p>episode two transcript</p></body></html>'
    }
  })
}

test('Library lists entries as cards and opens the Reader', async ({ harness }) => {
  await expectEngineState(harness.window, 'ready')
  await seedLibrary(harness)
  // A completing job refreshes the Library without a restart: emit job_done.
  await harness.mock.control('/job', {
    job: { id: 'done-job', source: 'https://example.com/episodes/2', state: 'done' },
    events: [
      { kind: 'job_done', step: null, message: 'done', data: { job_id: 'done-job' } }
    ]
  })
  const cards = harness.window.locator('.cards .card')
  await expect(cards).toHaveCount(2)
  // Sorted newest-first: Second Episode (later created_at) leads.
  await expect(cards.first().locator('.card-title')).toHaveText('Second Episode')
  await expect(cards.first().locator('.card-source')).toContainText('example.com')

  await cards.first().click()
  await expect(harness.window.locator('iframe.reader-frame')).toBeVisible()
  await expect(
    harness.window.frameLocator('iframe.reader-frame').locator('p')
  ).toHaveText('episode two transcript')
})

test('Library empty state shows a branded first-transcript CTA routing to New', async ({
  harness
}) => {
  await expectEngineState(harness.window, 'ready')
  // Default mock library is empty (no seed): the branded empty state renders.
  const empty = harness.window.locator('.empty-state')
  await expect(empty).toBeVisible()
  await expect(empty.locator('.empty-title')).toBeVisible()
  const cta = empty.locator('a.button-cta')
  await expect(cta).toHaveText('Transcribe your first episode')
  await cta.click()
  await expect(harness.window).toHaveURL(/#\/new$/)
  await expect(harness.window.locator('#new-source')).toBeVisible()
  // The full-screen drop overlay must be hidden unless dragging — otherwise it
  // (position:fixed; inset:0) visually covers the URL field. `toBeVisible()` on
  // the input can't catch this (pointer-events:none lets it pass), so assert the
  // overlay is hidden by default.
  await expect(harness.window.locator('.drop-hint')).toBeHidden()
})

test('New: pasted URL submits and shows live step progress, failure shows the hint', async ({
  harness
}) => {
  await expectEngineState(harness.window, 'ready')
  await harness.window.evaluate(() => {
    window.location.hash = '#/new'
  })
  await harness.window.locator('#new-source').fill('https://example.com/pod.mp3')
  await harness.window.locator('button[type="submit"]').click()

  const card = harness.window.locator('.job-card')
  await expect(card).toHaveCount(1)
  await expect(card.locator('.badge')).toHaveText('queued')

  const jobs = (await (await harness.mock.engine('/v1/jobs')).json()) as { id: string }[]
  expect(jobs).toHaveLength(1)
  const jobId = jobs[0]?.id ?? ''

  // Script the pipeline: step events stream in live over SSE.
  await harness.mock.control('/job', {
    job: { id: jobId, state: 'running' },
    events: [
      {
        kind: 'step_started',
        step: 'transcribe',
        message: 'transcribing audio',
        data: { job_id: jobId }
      }
    ]
  })
  await expect(card.locator('.badge')).toHaveText('running')
  await expect(card.locator('.step .step-name')).toHaveText('transcribe')

  // Structured failure: {code, message, hint} rendered from the job record.
  await harness.mock.control('/job', {
    job: { id: jobId, state: 'failed' },
    events: [
      {
        kind: 'job_failed',
        step: 'transcribe',
        message: 'Audio download failed',
        data: { job_id: jobId, code: 'download_failed', hint: 'Check the URL and try again.' }
      }
    ]
  })
  await expect(card.locator('.badge')).toHaveText('failed')
  await expect(card.locator('.job-error-message')).toContainText(
    'download_failed: Audio download failed'
  )
  await expect(card.locator('.job-error-hint')).toHaveText('Check the URL and try again.')
})

test('SSE drop recovers through hydration: state advances without a delivered event', async ({
  harness
}) => {
  await expectEngineState(harness.window, 'ready')
  await harness.window.evaluate(() => {
    window.location.hash = '#/new'
  })
  await harness.window.locator('#new-source').fill('https://example.com/recover.mp3')
  await harness.window.locator('button[type="submit"]').click()
  const card = harness.window.locator('.job-card')
  await expect(card.locator('.badge')).toHaveText('queued')
  const jobs = (await (await harness.mock.engine('/v1/jobs')).json()) as { id: string }[]
  const jobId = jobs[0]?.id ?? ''

  // Sever the stream, then advance the job SILENTLY (no SSE event): only a
  // reconnect-triggered hydration can deliver this state change.
  await harness.mock.control('/drop-sse', {})
  await harness.mock.control('/job', { job: { id: jobId, state: 'done' } })
  await expect(card.locator('.badge')).toHaveText('done', { timeout: 20_000 })
})

test('Settings: round-trip save, engine-side key test, inline validation error', async ({
  harness
}) => {
  await expectEngineState(harness.window, 'ready')
  await harness.window.evaluate(() => {
    window.location.hash = '#/settings'
  })
  const provider = harness.window.locator('#settings-chapter_provider')
  await expect(provider).toBeVisible()
  // Provider dropdown is fed by GET /v1/providers — the registry's one home.
  await expect(provider.locator('option')).toHaveCount(6)

  // Engine-side key test: the key goes to the engine, the verdict comes back.
  // A working entered key is persisted immediately (no separate Save needed) —
  // the result message confirms it, and the key reaches the vault-and-push.
  await harness.window.locator('#settings-api-key').fill('sk-test-123')
  await harness.window.getByRole('button', { name: 'Test key' }).click()
  await expect(harness.window.locator('.key-result').first()).toHaveText(
    'Key works — saved and set as your chapter provider.'
  )
  const log = await harness.mock.log()
  expect(log.some((entry) => entry.kind === 'keys-test' && entry.detail === 'anthropic')).toBe(
    true
  )
  // The successful Test auto-saved the key (keys-put), not just validated it.
  expect(log.some((entry) => entry.kind === 'keys-put' && entry.detail === 'anthropic')).toBe(true)

  // Save: settings PUT first, then the key goes through vault-and-push.
  await harness.window.locator('#settings-sentences').fill('7')
  await harness.window.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(harness.window.locator('.form-actions .key-result')).toHaveText('Saved.')
  const settings = (await (await harness.mock.engine('/v1/settings')).json()) as {
    sentences: number
  }
  expect(settings.sentences).toBe(7)
  const logAfterSave = await harness.mock.log()
  expect(
    logAfterSave.some((entry) => entry.kind === 'keys-put' && entry.detail === 'anthropic')
  ).toBe(true)

  // Engine 400 lands inline next to the offending field, and persists nothing.
  await provider.selectOption('custom')
  await harness.window.getByRole('button', { name: 'Save', exact: true }).click()
  const urlFieldError = harness.window.locator(
    '.field:has(#settings-custom_provider_url) .field-error'
  )
  await expect(urlFieldError).toBeVisible()
  await expect(urlFieldError).toContainText('custom provider requires a base URL')
  const unchanged = (await (await harness.mock.engine('/v1/settings')).json()) as {
    chapter_provider: string
  }
  expect(unchanged.chapter_provider).toBe('anthropic')
})
