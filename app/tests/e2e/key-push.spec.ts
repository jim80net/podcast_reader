import { expect, expectEngineState, test } from './fixtures'

/**
 * Key vault push-at-engine-start ordering (app-shell spec: safeStorage key
 * vault; task 3.2's e2e half): a saved key is pushed to the engine, and on
 * the next session every vaulted key reaches engine memory BEFORE the app
 * subscribes to events (the renderer hears "ready" only after the pushes).
 *
 * The cross-restart half needs a persistent vault: when the host offers no
 * OS-level encryption (headless Linux without a keyring), keys are
 * session-memory only by design, so that half is skipped with the reason
 * recorded.
 */

test('saved key is pushed now, and before the event stream on the next start', async ({
  harness
}, testInfo) => {
  await expectEngineState(harness.window, 'ready')
  await harness.window.evaluate(() => {
    window.location.hash = '#/settings'
  })
  await expect(harness.window.locator('#settings-chapter_provider')).toBeVisible()
  await harness.window.locator('#settings-api-key').fill('sk-vaulted-e2e')
  await harness.window.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(harness.window.locator('.form-actions .key-result')).toHaveText('Saved.')

  // The save routed the key through vault-and-push.
  const log = await harness.mock.log()
  expect(log.some((entry) => entry.kind === 'keys-put' && entry.detail === 'anthropic')).toBe(
    true
  )

  const storageMode = await harness.window.evaluate(() => window.api.keyStorageMode())
  if (storageMode !== 'encrypted') {
    testInfo.annotations.push({
      type: 'skip-partial',
      description:
        'safeStorage unavailable on this host: vault is session-memory only, ' +
        'so push-at-start cannot be asserted across a restart here'
    })
    return
  }

  // Fresh session, same userData (vault) — a NEW mock observes the start.
  await harness.relaunch()
  await expectEngineState(harness.window, 'ready')
  const restartLog = await harness.mock.log()
  const keyPush = restartLog.find(
    (entry) => entry.kind === 'keys-put' && entry.detail === 'anthropic'
  )
  const eventsOpen = restartLog.find((entry) => entry.kind === 'events-open')
  // Push-at-engine-start ordering: vaulted keys land before the app's own
  // /v1/events subscription (which only starts once status is "ready").
  expect(keyPush).toBeDefined()
  expect(eventsOpen).toBeDefined()
  expect(keyPush?.seq ?? Infinity).toBeLessThan(eventsOpen?.seq ?? -1)
})
