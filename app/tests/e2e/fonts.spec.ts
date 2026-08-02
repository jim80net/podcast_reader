import { expect, test } from './fixtures'

test('app-shell fonts are bundled, variable-weight capable, and local-only', async ({ harness }) => {
  await expect(harness.window.locator('.view h2')).toBeVisible()
  const proof = await harness.window.evaluate(async () => {
    const requested = [
      ['Source Sans 3', 400],
      ['Source Sans 3', 600],
      ['Source Sans 3', 700],
      ['Source Serif 4', 400],
      ['Source Serif 4', 600],
      ['Source Serif 4', 700]
    ] as const

    await Promise.all(requested.map(([family, weight]) => document.fonts.load(`${weight} 16px "${family}"`)))
    await document.fonts.ready

    return {
      checks: requested.map(([family, weight]) => ({
        family,
        weight,
        loaded: document.fonts.check(`${weight} 16px "${family}"`)
      })),
      bodyFamily: getComputedStyle(document.body).fontFamily,
      headingFamily: getComputedStyle(document.querySelector('.view h2')!).fontFamily,
      fontResources: performance
        .getEntriesByType('resource')
        .map((entry) => entry.name)
        .filter((name) => name.includes('.woff2'))
    }
  })

  expect(proof.checks).toEqual(
    expect.arrayContaining([
      expect.objectContaining({ family: 'Source Sans 3', weight: 400, loaded: true }),
      expect.objectContaining({ family: 'Source Sans 3', weight: 600, loaded: true }),
      expect.objectContaining({ family: 'Source Sans 3', weight: 700, loaded: true }),
      expect.objectContaining({ family: 'Source Serif 4', weight: 400, loaded: true }),
      expect.objectContaining({ family: 'Source Serif 4', weight: 600, loaded: true }),
      expect.objectContaining({ family: 'Source Serif 4', weight: 700, loaded: true })
    ])
  )
  expect(proof.bodyFamily).toMatch(/^"?Source Sans 3"?,/)
  expect(proof.headingFamily).toMatch(/^"?Source Serif 4"?,/)
  expect(proof.fontResources).toHaveLength(2)
  expect(proof.fontResources.every((url) => url.startsWith('file:'))).toBe(true)
})
