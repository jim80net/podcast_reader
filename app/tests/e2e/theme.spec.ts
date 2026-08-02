import { expect, expectEngineState, test } from './fixtures'

test('fresh OS-dark launch starts Light while explicit System still follows the OS', async ({
  harness
}) => {
  await expectEngineState(harness.window, 'ready')
  await harness.window.emulateMedia({ colorScheme: 'dark' })
  await harness.window.evaluate(() => localStorage.removeItem('pr.theme'))
  await harness.window.addInitScript(() => {
    const changes: string[] = []
    ;(window as typeof window & { __themePaints?: string[] }).__themePaints = changes
    new MutationObserver(() => {
      const theme = document.documentElement?.dataset['theme']
      if (theme !== undefined && changes.length === 0) changes.push(theme)
    }).observe(document, {
      attributes: true,
      attributeFilter: ['data-theme'],
      childList: true,
      subtree: true
    })
  })

  await harness.window.reload()
  await expect(harness.window.locator('html')).toHaveAttribute('data-theme', 'light')
  const firstPaint = await harness.window.evaluate(
    () => (window as typeof window & { __themePaints?: string[] }).__themePaints?.[0]
  )
  expect(firstPaint).toBe('light')

  const control = harness.window.getByLabel('Theme')
  await expect(control).toHaveValue('light')
  await control.selectOption('system')
  await expect(harness.window.locator('html')).toHaveAttribute('data-theme', 'dark')
  expect(await harness.window.evaluate(() => localStorage.getItem('pr.theme'))).toBe('system')

  await harness.window.emulateMedia({ colorScheme: 'light' })
  await expect(harness.window.locator('html')).toHaveAttribute('data-theme', 'light')
  await control.selectOption('dark')
  await expect(harness.window.locator('html')).toHaveAttribute('data-theme', 'dark')
})

test('Settings and header theme controls stay bound to one preference', async ({ harness }) => {
  await expectEngineState(harness.window, 'ready')
  await harness.window.evaluate(() => {
    window.location.hash = '#/settings'
  })

  const header = harness.window.locator('.theme-select')
  const settings = harness.window.locator('#settings-theme')
  await expect(settings).toHaveValue('light')

  await settings.selectOption('dark')
  await expect(header).toHaveValue('dark')
  await expect(harness.window.locator('html')).toHaveAttribute('data-theme', 'dark')

  await header.selectOption('system')
  await expect(settings).toHaveValue('system')
  expect(await harness.window.evaluate(() => localStorage.getItem('pr.theme'))).toBe('system')
})
