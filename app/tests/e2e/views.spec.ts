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

test('Library searches private transcript text, clears, and opens the result', async ({ harness }) => {
  await expectEngineState(harness.window, 'ready')
  await seedLibrary(harness)
  await harness.window.evaluate(() => {
    window.location.hash = '#/new'
  })
  await expect(harness.window.locator('#new-source')).toBeVisible()
  await harness.window.evaluate(() => {
    window.location.hash = '#/library'
  })
  await expect(harness.window.locator('.cards .card')).toHaveCount(2)

  const input = harness.window.getByRole('searchbox', { name: 'Search transcripts' })
  await expect(input).toHaveAttribute('autocomplete', 'off')
  await expect(input).toHaveAttribute('spellcheck', 'false')
  await expect(input).not.toHaveAttribute('name')
  await input.fill('episode two transcript')
  await expect(harness.window.locator('.library-search-status')).toHaveText('1 match.')
  const results = harness.window.locator('.cards .search-result')
  await expect(results).toHaveCount(1)
  await expect(results.first().locator('.card-title')).toHaveText('Second Episode')
  await expect(results.first().locator('.search-result-excerpt')).toContainText(
    'episode two transcript'
  )
  await expect(harness.window).not.toHaveURL(/episode%20two|episode two/)

  const privacy = await harness.window.evaluate(() => ({
    attributes: Array.from(document.querySelectorAll('*')).flatMap((node) =>
      Array.from(node.attributes, (attribute) => `${attribute.name}=${attribute.value}`)
    ),
    localStorage: JSON.stringify(localStorage),
    sessionStorage: JSON.stringify(sessionStorage),
    windowName: window.name
  }))
  expect(privacy.attributes.join('\n')).not.toContain('episode two transcript')
  expect(privacy.localStorage).not.toContain('episode two transcript')
  expect(privacy.sessionStorage).not.toContain('episode two transcript')
  expect(privacy.windowName).not.toContain('episode two transcript')

  const log = await harness.mock.log()
  expect(log).toContainEqual(expect.objectContaining({ kind: 'library-search', detail: 'performed' }))
  expect(JSON.stringify(log)).not.toContain('episode two transcript')

  await harness.window.getByRole('button', { name: 'Clear' }).click()
  await expect(input).toHaveValue('')
  await expect(input).toBeFocused()
  await expect(harness.window.locator('.cards .card')).toHaveCount(2)

  await input.fill('episode two transcript')
  await expect(results).toHaveCount(1)
  await results.first().click()
  await expect(harness.window.locator('iframe.reader-frame')).toBeVisible()
  await harness.window.evaluate(() => {
    window.location.hash = '#/library'
  })
  await expect(input).toHaveValue('')
  await expect(harness.window.locator('.cards .card')).toHaveCount(2)
})

test('Library search suppresses stale work, retries busy, and reports completeness', async ({
  harness
}) => {
  await expectEngineState(harness.window, 'ready')
  await seedLibrary(harness)
  await harness.mock.control('/seed', {
    searchResponses: {
      'slow old': [
        {
          delay_ms: 800,
          response: {
            results: [{ source_id: 'ep-one', title: 'Stale result', excerpt: 'must not paint' }],
            has_more: false,
            partial: false
          }
        }
      ],
      current: [
        { status: 429 },
        {
          response: {
            results: [{ source_id: 'ep-two', title: 'Current result', excerpt: 'current text' }],
            has_more: true,
            partial: true
          }
        }
      ],
      recover: [
        { status: 500 },
        { response: { results: [], has_more: false, partial: false } }
      ],
      'stale busy': [
        { status: 429 },
        { response: { results: [], has_more: false, partial: false } }
      ],
      fresh: [
        {
          response: {
            results: [{ source_id: 'ep-one', title: 'Fresh result', excerpt: 'fresh text' }],
            has_more: false,
            partial: false
          }
        }
      ]
    }
  })
  await harness.window.evaluate(() => {
    window.location.hash = '#/new'
  })
  await expect(harness.window.locator('#new-source')).toBeVisible()
  await harness.window.evaluate(() => {
    window.location.hash = '#/library'
  })
  const input = harness.window.getByRole('searchbox', { name: 'Search transcripts' })
  await input.fill('slow old')
  await harness.window.waitForTimeout(350)
  await input.fill('current')
  await expect(harness.window.locator('.library-search-status')).toHaveText(
    '1 match. Showing the first 20 matches. Some transcripts could not be searched.',
    { timeout: 5_000 }
  )
  await expect(harness.window.locator('.cards .search-result')).toHaveCount(1)
  await expect(harness.window.locator('.cards')).toContainText('Current result')
  await expect(harness.window.locator('.cards')).not.toContainText('Stale result')

  const callsBeforeShortQuery = (await harness.mock.log()).filter(
    (entry) => entry.kind === 'library-search'
  ).length
  await input.fill('😀')
  await expect(harness.window.locator('.library-search-status')).toHaveText(
    'Enter at least 2 characters.'
  )
  await expect(harness.window.locator('.cards .card')).toHaveCount(2)
  await harness.window.waitForTimeout(350)
  const callsAfterShortQuery = (await harness.mock.log()).filter(
    (entry) => entry.kind === 'library-search'
  ).length
  expect(callsAfterShortQuery).toBe(callsBeforeShortQuery)

  await input.fill('😀'.repeat(51))
  await expect(harness.window.locator('.library-search-status')).toHaveText('0 matches.')
  const callsAfterValidAstralQuery = (await harness.mock.log()).filter(
    (entry) => entry.kind === 'library-search'
  ).length
  expect(callsAfterValidAstralQuery).toBe(callsAfterShortQuery + 1)

  await input.fill('recover')
  await expect(harness.window.getByRole('button', { name: 'Retry' })).toBeVisible()
  await harness.window.getByRole('button', { name: 'Retry' }).click()
  await expect(harness.window.locator('.library-search-status')).toHaveText('0 matches.')
  await expect(harness.window.locator('.empty-search')).toHaveText('No transcript matches.')

  await input.fill('stale busy')
  await harness.window.waitForTimeout(350)
  await input.fill('fresh')
  await expect(harness.window.locator('.library-search-status')).toHaveText('1 match.')
  await harness.window.waitForTimeout(1100)
  const countsResponse = await harness.mock.control('/search-counts')
  const counts = (await countsResponse.json()) as Record<string, number>
  expect(counts['stale busy']).toBe(1)
  expect(counts.fresh).toBe(1)
  await expect(harness.window.locator('.cards')).toContainText('Fresh result')
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
  await expect(card.locator('.job-row-key', { hasText: 'transcribe' })).toBeVisible()

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

test('New: a finished job links to its transcript', async ({ harness }) => {
  await expectEngineState(harness.window, 'ready')
  const source = 'https://example.com/done.mp3'
  // The library entry the engine would create on completion (keyed by source).
  await harness.mock.control('/seed', {
    library: [
      {
        source_id: 'done-src-id',
        source,
        title: 'Done Episode',
        html_path: '/mock/done.html',
        created_at: 1_700_000_000
      }
    ],
    transcripts: { 'done-src-id': '<!DOCTYPE html><html><body><p>done transcript</p></body></html>' }
  })
  await harness.window.evaluate(() => {
    window.location.hash = '#/new'
  })
  await harness.window.locator('#new-source').fill(source)
  await harness.window.locator('button[type="submit"]').click()
  const card = harness.window.locator('.job-card')
  const jobs = (await (await harness.mock.engine('/v1/jobs')).json()) as { id: string }[]
  const jobId = jobs[0]?.id ?? ''

  await harness.mock.control('/job', {
    job: { id: jobId, state: 'done' },
    events: [{ kind: 'job_done', step: null, message: 'done', data: { job_id: jobId } }]
  })
  await expect(card.locator('.badge')).toHaveText('done')
  // The header is the library title and IS the link to the transcript.
  const title = card.locator('a.job-title')
  await expect(title).toHaveText('Done Episode')
  await expect(title).toHaveAttribute('href', '#/reader/done-src-id')
  await title.click()
  await expect(harness.window.locator('iframe.reader-frame')).toBeVisible()
})

test('New: a job can be rerun with a different chapter model', async ({ harness }) => {
  await expectEngineState(harness.window, 'ready')
  const source = 'https://example.com/rerun.mp3'
  await harness.window.evaluate(() => {
    window.location.hash = '#/new'
  })
  await harness.window.locator('#new-source').fill(source)
  await harness.window.locator('button[type="submit"]').click()
  const jobs = (await (await harness.mock.engine('/v1/jobs')).json()) as { id: string }[]
  const jobId = jobs[0]?.id ?? ''
  // Drive it to failed so the Rerun affordance shows.
  await harness.mock.control('/job', {
    job: { id: jobId, state: 'failed' },
    events: [
      {
        kind: 'job_failed',
        step: 'transcribe',
        message: 'boom',
        data: { job_id: jobId, code: 'internal', hint: '' }
      }
    ]
  })
  const card = harness.window.locator('.job-card')
  await expect(card.locator('.badge')).toHaveText('failed')

  // Open the rerun dialog, enable the chapter section, pick a provider, rerun.
  await card.getByRole('button', { name: 'Rerun with a different model…' }).click()
  const dialog = harness.window.locator('dialog.rerun-dialog')
  await expect(dialog).toBeVisible()
  await dialog.locator('#rerun-chapter').check()
  await dialog.locator('select').selectOption('openai')
  await dialog.getByRole('button', { name: 'Rerun', exact: true }).click()
  await expect(dialog).toBeHidden()

  // The resubmission carried the chapter-provider override to the engine.
  const after = (await (await harness.mock.engine('/v1/jobs')).json()) as {
    source: string
    overrides: { chapter_provider?: string } | null
  }[]
  const rerun = after.find((j) => j.overrides?.chapter_provider === 'openai')
  expect(rerun?.source).toBe(source)
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
  const cleanup = harness.window.locator('#settings-caption-cleanup')
  await expect(cleanup).not.toBeChecked()
  await cleanup.check()
  await harness.window.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(harness.window.locator('.form-actions .key-result')).toHaveText('Saved.')
  const settings = (await (await harness.mock.engine('/v1/settings')).json()) as {
    sentences: number
    caption_cleanup: boolean
  }
  expect(settings.sentences).toBe(7)
  expect(settings.caption_cleanup).toBe(true)
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

test('Settings: add, use, and remove a named OpenAI-compatible provider', async ({ harness }) => {
  await expectEngineState(harness.window, 'ready')
  await harness.window.evaluate(() => {
    window.location.hash = '#/settings'
  })

  await harness.window.getByRole('button', { name: 'Add provider' }).click()
  await harness.window.locator('#settings-named_provider_name').fill('office-gateway')
  await harness.window
    .locator('#settings-named_provider_url')
    .fill('https://llm.corp.example/v1')
  await harness.window.locator('#settings-named_provider_model').fill('corp-small')
  await harness.window.locator('#settings-named_provider_tokens').fill('32768')
  await harness.window.locator('#settings-named_provider_key').fill('  sk-office-save  ')
  await harness.window.getByRole('button', { name: 'Save provider', exact: true }).click()
  await expect(harness.window.getByText('Provider and key saved.')).toBeVisible()

  await harness.window
    .locator('#settings-named_provider_url')
    .fill('https://llm.corp.example/v2')
  await harness.window.locator('#settings-named_provider_key').fill('  sk-office-test  ')
  await harness.mock.control('/seed', { keyTestDelayMs: 200 })
  await harness.window.getByRole('button', { name: 'Save provider and test key' }).click()
  await expect(harness.window.getByRole('button', { name: 'Remove provider' })).toBeDisabled()
  await expect(harness.window.getByText('Provider and key saved.')).toBeVisible()

  const provider = harness.window.locator('#settings-chapter_provider')
  await expect(provider.locator('option')).toHaveCount(7)
  await provider.selectOption('office-gateway')
  const log = await harness.mock.log()
  expect(
    log.some((entry) => entry.kind === 'keys-test' && entry.detail === 'office-gateway')
  ).toBe(true)
  expect(
    log.some((entry) => entry.kind === 'keys-put' && entry.detail === 'office-gateway')
  ).toBe(true)

  // Editing config is persisted before the test; a failed test leaves the new
  // nonsecret config saved but never stores the entered replacement key.
  const keyPushesBeforeFailure = log.filter(
    (entry) => entry.kind === 'keys-put' && entry.detail === 'office-gateway'
  ).length
  await harness.mock.control('/seed', {
    keyTestDelayMs: 0,
    keyTestResult: { ok: false, detail: '401 from provider' }
  })
  await harness.window
    .locator('#settings-named_provider_url')
    .fill('https://new-gateway.example/v1')
  await harness.window.locator('#settings-named_provider_key').fill('sk-must-not-store')
  await harness.window.getByRole('button', { name: 'Save provider and test key' }).click()
  await expect(harness.window.getByText('Provider saved; key test failed: 401')).toBeVisible()
  const logAfterFailure = await harness.mock.log()
  expect(
    logAfterFailure.filter(
      (entry) => entry.kind === 'keys-put' && entry.detail === 'office-gateway'
    )
  ).toHaveLength(keyPushesBeforeFailure)
  const edited = (await (await harness.mock.engine('/v1/settings')).json()) as {
    custom_providers: Array<{ base_url: string }>
  }
  expect(edited.custom_providers[0]?.base_url).toBe('https://new-gateway.example/v1')

  await harness.mock.control('/seed', { settingsPutDelayMs: 200 })
  await harness.window.locator('#settings-sentences').fill('9')
  await harness.window.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(harness.window.getByRole('button', { name: 'Add provider' })).toBeDisabled()
  await expect(
    harness.window.getByRole('button', { name: 'Save provider and test key' })
  ).toBeDisabled()
  await expect(harness.window.locator('.form-actions .key-result')).toHaveText('Saved.')

  await harness.window.getByRole('button', { name: 'Remove provider' }).click()
  await expect(harness.window.getByRole('button', { name: 'Add provider' })).toBeDisabled()
  await expect(
    harness.window.getByRole('button', { name: 'Save provider and test key' })
  ).toBeDisabled()
  await expect(harness.window.getByRole('button', { name: 'Save', exact: true })).toBeDisabled()
  await expect(harness.window.getByRole('button', { name: 'Test key', exact: true })).toBeDisabled()
  await expect(provider.locator('option')).toHaveCount(6)
  const settings = (await (await harness.mock.engine('/v1/settings')).json()) as {
    custom_providers: unknown[]
    chapter_provider: string
  }
  expect(settings.custom_providers).toEqual([])
  expect(settings.chapter_provider).toBe('anthropic')
})
