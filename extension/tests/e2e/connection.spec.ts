import { createServer } from 'node:net'

import { expect, storedPairing, test } from './fixtures'

/**
 * Connection-state e2e (task 8.1, ext-pairing spec, reconnection
 * requirement): a stored pairing whose port refuses connection renders the
 * app-not-running state with the launch affordance — and KEEPS the pairing
 * (the engine port is fixed per install; the app coming back is the fix).
 */

/** A port that is certainly closed: bind an ephemeral one, then release it. */
async function closedPort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = createServer()
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      if (typeof address !== 'object' || address === null) {
        reject(new Error('no address'))
        return
      }
      const port = address.port
      server.close(() => resolve(port))
    })
  })
}

test('engine down: popup keeps the pairing and offers the launch affordance', async ({
  harness
}) => {
  const deadPort = await closedPort()
  const popup = await harness.openPopup()
  await harness.seedStorage(popup, { port: deadPort, token: 'stale-but-kept' })

  await expect(popup.locator('#engine-down')).toBeVisible()
  await expect(popup.locator('#launch-app')).toBeVisible()
  // No re-pair form: connection failure is not an auth failure.
  await expect(popup.locator('#pair-combined')).toHaveCount(0)

  // The stored pairing survives (re-pairing is only for 401/rotation).
  expect(await storedPairing(popup)).toEqual({ port: deadPort, token: 'stale-but-kept' })

  // "Try again" re-probes: still down, same state (no crash, no clearing).
  await popup.click('#retry-probe')
  await expect(popup.locator('#engine-down')).toBeVisible()
})
