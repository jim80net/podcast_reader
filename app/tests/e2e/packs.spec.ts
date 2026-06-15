import { expect, expectEngineState, test } from './fixtures'
import type { Harness } from './fixtures'

/**
 * Setup wizard + Settings Packs flows against the mock engine (app-setup-ui
 * spec, task 6.4): first-run auto-open with recommended packs pre-checked and
 * device defaulting (per S4), install progress over forwarded pack events,
 * hydration-after-navigation (lossless wizard), resumable resume, skip and
 * re-run from Settings, install/uninstall with the 409 reason surfaced (per
 * S1), the incompatible re-download affordance (per S8), and the
 * cuda-without-pack advisory (per S4/Q2).
 */

/** Recommended packs missing → the wizard's first-run trigger fires. */
const WIZARD_SEED = {
  packs: [
    { id: 'cuda-runtime', state: 'not-installed', installed_version: null },
    { id: 'model-large-v3', state: 'not-installed', installed_version: null }
  ]
}

const wizardHeading = (harness: Harness) =>
  harness.window.locator('h2', { hasText: 'Welcome to Podcast Reader' })

async function expectWizardOpen(harness: Harness): Promise<void> {
  await expectEngineState(harness.window, 'ready')
  await expect(wizardHeading(harness)).toBeVisible({ timeout: 15_000 })
}

function packRow(harness: Harness, packId: string) {
  return harness.window.locator(`.pack-row[data-pack-id="${packId}"]`)
}

/** Script a pack's authoritative state and broadcast the matching pack_state event. */
async function scriptPackState(
  harness: Harness,
  packId: string,
  state: string,
  patch: Record<string, unknown> = {}
): Promise<void> {
  await harness.mock.control('/pack', {
    pack: { id: packId, state, ...patch },
    events: [
      // Per Q5: pack events carry pack_id and never job_id.
      { kind: 'pack_state', step: null, message: state, data: { pack_id: packId, state } }
    ]
  })
}

test.describe('setup wizard (first run)', () => {
  test.use({ mockSeed: WIZARD_SEED })

  test('auto-opens, pre-checks recommended packs with sizes, installs with device defaulting and live progress', async ({
    harness
  }) => {
    test.setTimeout(120_000)
    await expectWizardOpen(harness)

    // Hardware summary + per-S4 device note from the mock's win32+NVIDIA block.
    await expect(harness.window.locator('.hardware-summary')).toHaveText(
      'Windows — NVIDIA GPU: Mock GeForce RTX'
    )
    await expect(harness.window.locator('.device-note')).toContainText('device: cuda')

    // Recommended packs pre-checked with their download sizes; others not.
    await expect(harness.window.locator('#setup-pack-cuda-runtime')).toBeChecked()
    await expect(harness.window.locator('#setup-pack-model-large-v3')).toBeChecked()
    await expect(harness.window.locator('#setup-pack-model-tiny')).not.toBeChecked()
    await expect(packRow(harness, 'cuda-runtime').locator('.pack-size')).toHaveText('1.2 GB')
    await expect(packRow(harness, 'model-large-v3').locator('.pack-size')).toHaveText('3.1 GB')
    // The unpublished pack never shows up as a first-run choice (per S5).
    await expect(packRow(harness, 'diarization')).toHaveCount(0)

    await harness.window.locator('#setup-install').click()

    // Device defaulting (per S4): whisper_device PUT from detected hardware.
    await expect
      .poll(async () => {
        const settings = (await (await harness.mock.engine('/v1/settings')).json()) as {
          whisper_device: string
        }
        return settings.whisper_device
      })
      .toBe('cuda')
    const log = await harness.mock.log()
    expect(log.some((e) => e.kind === 'pack-install' && e.detail === 'cuda-runtime')).toBe(true)
    expect(log.some((e) => e.kind === 'pack-install' && e.detail === 'model-large-v3')).toBe(true)

    // Live progress from a forwarded pack_progress event.
    await expect(packRow(harness, 'cuda-runtime').locator('.pack-state')).toHaveText('installing')
    await harness.mock.control('/pack', {
      pack: {
        id: 'cuda-runtime',
        state: 'installing',
        progress: { bytes: 622_000_000, total: 1_243_159_663 }
      },
      events: [
        {
          kind: 'pack_progress',
          step: null,
          message: '',
          data: { pack_id: 'cuda-runtime', bytes: 622_000_000, total: 1_243_159_663 }
        }
      ]
    })
    await expect(packRow(harness, 'cuda-runtime').locator('.pack-progress-text')).toHaveText(
      '622 MB of 1.2 GB'
    )

    // Completion: both packs land installed, Finish appears, flag set on click.
    await scriptPackState(harness, 'cuda-runtime', 'installed', {
      progress: null,
      installed_version: '1'
    })
    await scriptPackState(harness, 'model-large-v3', 'installed', {
      progress: null,
      installed_version: 'mock-rev'
    })
    await expect(packRow(harness, 'cuda-runtime').locator('.pack-state')).toHaveText('installed')
    const finish = harness.window.locator('#setup-finish')
    await expect(finish).toBeVisible()
    await finish.click()
    await expect(harness.window.locator('h2', { hasText: 'Library' })).toBeVisible()

    // Completing set the first-run flag: a relaunch against a fresh mock with
    // the SAME missing-pack seed must not reopen the wizard.
    await harness.relaunch()
    await expectEngineState(harness.window, 'ready')
    await expect(harness.window.locator('h2', { hasText: 'Library' })).toBeVisible()
    await harness.window.waitForTimeout(1000) // would-be auto-navigation window
    await expect(wizardHeading(harness)).toHaveCount(0)
  })

  test('skip is honored across restarts and setup is re-runnable from Settings', async ({
    harness
  }) => {
    test.setTimeout(120_000)
    await expectWizardOpen(harness)
    await harness.window.locator('#setup-skip').click()
    await expect(harness.window.locator('h2', { hasText: 'Library' })).toBeVisible()

    // Settings offers "Run setup again" (the wizard is never one-shot).
    await harness.window.evaluate(() => {
      window.location.hash = '#/settings'
    })
    const rerun = harness.window.locator('#settings-run-setup')
    await expect(rerun).toBeVisible()
    await rerun.click()
    await expect(wizardHeading(harness)).toBeVisible()

    // Skipping set the flag: no wizard on the next launch.
    await harness.relaunch()
    await expectEngineState(harness.window, 'ready')
    await expect(harness.window.locator('h2', { hasText: 'Library' })).toBeVisible()
    await harness.window.waitForTimeout(1000)
    await expect(wizardHeading(harness)).toHaveCount(0)
  })

  test('optional AI-model section renders, custom reveals the URL field, and Skip finishes with no key', async ({
    harness
  }) => {
    await expectWizardOpen(harness)

    // The optional chapter-provider section is offered with a provider
    // dropdown (built-ins + custom) fed by GET /v1/providers.
    const provider = harness.window.locator('#setup-chapter-provider')
    await expect(provider).toBeVisible()
    await expect(provider.locator('option')).toHaveCount(6)
    // The base-URL field is hidden until the custom provider is chosen.
    await expect(harness.window.locator('#setup-chapter-custom-url')).toBeHidden()
    await provider.selectOption('custom')
    await expect(harness.window.locator('#setup-chapter-custom-url')).toBeVisible()

    // Skip still completes setup with NO key set — the no-block guarantee.
    await harness.window.locator('#setup-skip').click()
    await expect(harness.window.locator('h2', { hasText: 'Library' })).toBeVisible()
    const log = await harness.mock.log()
    expect(log.some((entry) => entry.kind === 'keys-put')).toBe(false)
  })

  test('install progress survives navigation away and back (hydrated from GET /v1/packs)', async ({
    harness
  }) => {
    await expectWizardOpen(harness)

    // Advance the engine-side state SILENTLY (no SSE event): only mount-time
    // hydration can deliver this — exactly what lossless navigation relies on.
    await harness.mock.control('/pack', {
      pack: {
        id: 'model-large-v3',
        state: 'installing',
        progress: { bytes: 1_000_000_000, total: 3_090_835_702 }
      }
    })
    await harness.window.evaluate(() => {
      window.location.hash = '#/library'
    })
    await expect(harness.window.locator('h2', { hasText: 'Library' })).toBeVisible()
    await harness.window.evaluate(() => {
      window.location.hash = '#/setup'
    })
    const row = packRow(harness, 'model-large-v3')
    await expect(row.locator('.pack-state')).toHaveText('installing')
    await expect(row.locator('.pack-progress-text')).toHaveText('1.0 GB of 3.1 GB')
    await expect(row.locator('.progress')).toHaveAttribute('aria-valuenow', '32')
  })
})

test.describe('setup wizard (resumable pack)', () => {
  test.use({
    mockSeed: {
      packs: [{ id: 'model-large-v3', state: 'resumable', installed_version: null }]
    }
  })

  test('an interrupted download is shown resumable and one action continues it', async ({
    harness
  }) => {
    await expectWizardOpen(harness)
    const row = packRow(harness, 'model-large-v3')
    await expect(row.locator('.pack-state')).toHaveText('resumable')
    await expect(row.locator('.pack-note')).toContainText('resumes where it left off')
    await expect(harness.window.locator('#setup-pack-model-large-v3')).toBeChecked()

    await harness.window.locator('#setup-install').click()
    await expect(row.locator('.pack-state')).toHaveText('installing')
    const log = await harness.mock.log()
    expect(log.some((e) => e.kind === 'pack-install' && e.detail === 'model-large-v3')).toBe(true)
  })
})

test.describe('Settings packs section', () => {
  // Defaults: recommended packs installed (no wizard), whisper_device=cuda;
  // model-medium seeded incompatible for the re-download affordance (per S8).
  test.use({
    mockSeed: {
      packs: [
        {
          id: 'model-medium',
          state: 'incompatible',
          installed_version: '0',
          error: {
            code: 'incompatible',
            message: 'component cudnn version 8 is outside the required major version 9'
          }
        }
      ]
    }
  })

  async function openSettings(harness: Harness): Promise<void> {
    await expectEngineState(harness.window, 'ready')
    await harness.window.evaluate(() => {
      window.location.hash = '#/settings'
    })
    await expect(harness.window.locator('.packs-header h3')).toHaveText('Packs')
  }

  test('lists every pack incl. unavailable, installs, and uninstalls without touching the device setting', async ({
    harness
  }) => {
    test.setTimeout(120_000)
    await openSettings(harness)

    // All six registry packs listed; the unpublished one is `unavailable`
    // with no actions (per S5).
    await expect(harness.window.locator('.packs-section .pack-row')).toHaveCount(6)
    const diarization = packRow(harness, 'diarization')
    await expect(diarization.locator('.pack-state')).toHaveText('Coming soon')
    await expect(diarization.locator('button')).toHaveCount(0)

    // No advisory while the CUDA pack is installed and usable.
    await expect(harness.window.locator('.cuda-advisory')).toBeHidden()

    // License attributions render from the engine-sent notices (task 8.1).
    const cudaLicenses = packRow(harness, 'cuda-runtime').locator('.pack-licenses')
    await expect(cudaLicenses.locator('summary')).toHaveText('License attributions')
    await cudaLicenses.locator('summary').click()
    await expect(cudaLicenses.locator('.pack-license')).toHaveCount(2)
    await expect(cudaLicenses.locator('.pack-license').first()).toContainText('NVIDIA cuBLAS')
    // Packs without engine-sent notices render no attribution block.
    await expect(packRow(harness, 'model-tiny').locator('.pack-licenses')).toHaveCount(0)

    // Install a model pack; progress state arrives over the event stream.
    await packRow(harness, 'model-tiny').getByRole('button', { name: 'Install' }).click()
    await expect(packRow(harness, 'model-tiny').locator('.pack-state')).toHaveText('installing')
    await scriptPackState(harness, 'model-tiny', 'installed', {
      progress: null,
      installed_version: 'mock-rev'
    })
    await expect(packRow(harness, 'model-tiny').locator('.pack-state')).toHaveText('installed')
    await expect(packRow(harness, 'model-tiny').locator('.pack-size')).toContainText('vmock-rev')

    // Uninstall the CUDA pack: the cuda advisory appears (per S4/Q2) and the
    // engine's whisper_device is NOT mutated.
    await packRow(harness, 'cuda-runtime').getByRole('button', { name: 'Uninstall' }).click()
    await expect(packRow(harness, 'cuda-runtime').locator('.pack-state')).toHaveText(
      'not-installed'
    )
    await expect(harness.window.locator('.cuda-advisory')).toBeVisible()
    await expect(harness.window.locator('.cuda-advisory')).toContainText('run on the CPU')
    const settings = (await (await harness.mock.engine('/v1/settings')).json()) as {
      whisper_device: string
    }
    expect(settings.whisper_device).toBe('cuda')
  })

  test('surfaces the engine 409 uninstall refusal and offers re-download for incompatible packs', async ({
    harness
  }) => {
    test.setTimeout(120_000)
    await openSettings(harness)

    // Incompatible pack: structured error + one-action re-download (per S8).
    const medium = packRow(harness, 'model-medium')
    await expect(medium.locator('.pack-error').first()).toContainText(
      'incompatible: component cudnn version 8'
    )
    await medium.getByRole('button', { name: 'Re-download' }).click()
    await expect(medium.locator('.pack-state')).toHaveText('installing')
    const log = await harness.mock.log()
    expect(log.some((e) => e.kind === 'pack-install' && e.detail === 'model-medium')).toBe(true)

    // Uninstall refusal (per S1: 409 only while installing): flip the pack to
    // installing engine-side WITHOUT an event, so the UI still offers
    // Uninstall — the click races the install, exactly the 409 case.
    await scriptPackState(harness, 'model-small', 'installed', { installed_version: 'mock-rev' })
    const small = packRow(harness, 'model-small')
    await expect(small.locator('.pack-state')).toHaveText('installed')
    await harness.mock.control('/pack', {
      pack: { id: 'model-small', state: 'installing', progress: { bytes: 1, total: 486_212_372 } }
    })
    await small.getByRole('button', { name: 'Uninstall' }).click()
    await expect(small.locator('.pack-error').last()).toContainText(
      "pack 'model-small' is currently installing; uninstall is refused"
    )
    // The refusal left the pack present (now visibly installing).
    await expect(small.locator('.pack-state')).toHaveText('installing')
  })
})
