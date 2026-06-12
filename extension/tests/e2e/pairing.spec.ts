import { expect, storedPairing, test } from './fixtures'

/**
 * Pairing e2e (task 8.1, ext-pairing spec): the real popup against the mock
 * engine's `/v1/pair/claim` — happy path (seeded code → claim → authed
 * health verify → `chrome.storage.local`) and the uniform-403 wrong-code
 * rejection, asserting the attempt budget through the `/__mock/pairing`
 * seam (the real engine never exposes the pending code; the mock does, to
 * the test runner only).
 */

interface MockPairing {
  code: string | null
  expires_at: number
  failed_attempts: number
}

test('pairing happy path: combined paste string claims, verifies, and stores {port, token}', async ({
  harness
}) => {
  await harness.mock.control('/seed', { pairing: { code: 'ABC234' } })

  const popup = await harness.openPopup()
  await expect(popup.locator('#pair-combined')).toBeVisible()
  await popup.fill('#pair-combined', `${harness.mock.port}-ABC234`)
  await popup.click('#pair-submit')

  // Connected state: the pairing form is replaced by the connected view
  // (its job list renders only after claim + authed health succeed).
  await expect(popup.locator('#job-list')).toHaveCount(1)
  await expect(popup.locator('#pair-combined')).toHaveCount(0)

  // The verified pairing landed in chrome.storage.local (ext-pairing spec).
  expect(await storedPairing(popup)).toEqual({
    port: harness.mock.port,
    token: harness.mock.token
  })

  // Single-use: the claim consumed the pending code.
  const pairing = (await (await harness.mock.control('/pairing')).json()) as MockPairing
  expect(pairing.code).toBeNull()
})

test('wrong code: uniform 403 burns one attempt, stores nothing, offers retry', async ({
  harness
}) => {
  await harness.mock.control('/seed', { pairing: { code: 'ABC234' } })

  const popup = await harness.openPopup()
  await popup.fill('#pair-combined', `${harness.mock.port}-ZZZZZZ`)
  await popup.click('#pair-submit')

  // Self-authored rejection copy; the form stays up for a retry.
  await expect(popup.locator('#pair-error')).toBeVisible()
  await expect(popup.locator('#pair-error')).toContainText('not accepted')
  await expect(popup.locator('#pair-submit')).toBeEnabled()

  // Nothing stored; the failed attempt hit the budget but the code is
  // still pending (1 of 5), so a corrected retry succeeds.
  expect(await storedPairing(popup)).toBeNull()
  const pairing = (await (await harness.mock.control('/pairing')).json()) as MockPairing
  expect(pairing).toMatchObject({ code: 'ABC234', failed_attempts: 1 })

  await popup.fill('#pair-combined', `${harness.mock.port}-ABC234`)
  await popup.click('#pair-submit')
  await expect(popup.locator('#job-list')).toHaveCount(1)
  expect(await storedPairing(popup)).toEqual({
    port: harness.mock.port,
    token: harness.mock.token
  })
})
