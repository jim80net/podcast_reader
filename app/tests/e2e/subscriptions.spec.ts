import { expect, test } from './fixtures'

test('Local ordinary views make no subscription calls and the explicit view is read-only', async ({ harness }) => {
  const { window, mock } = harness
  await expect(window.locator('.engine-pill')).toHaveAttribute('data-state', 'ready')

  await window.getByRole('link', { name: 'New' }).click()
  await window.getByRole('link', { name: 'Settings' }).click()
  await window.getByRole('link', { name: 'Library' }).click()
  let requests = (await mock.log()).filter((entry) => entry.kind === 'request').map((entry) => entry.detail)
  expect(requests.some((request) => request.includes('/v1/subscriptions'))).toBe(false)
  expect(requests.some((request) => request.includes('/v1/online-capabilities'))).toBe(false)

  await window.getByRole('link', { name: 'Subscriptions' }).click()
  await expect(window.getByText('Connect a premium account to add or poll subscriptions.')).toBeVisible()
  await expect(window.getByRole('button', { name: 'Add podcast' })).toBeDisabled()
  requests = (await mock.log()).filter((entry) => entry.kind === 'request').map((entry) => entry.detail)
  expect(requests.filter((request) => request === 'GET /v1/subscriptions')).toHaveLength(1)
  expect(requests.some((request) => /POST|DELETE|PUT/.test(request) && request.includes('/v1/subscriptions'))).toBe(false)
})
