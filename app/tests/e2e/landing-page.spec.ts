import { chromium, expect, test } from '@playwright/test'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

const landingPageUrl = pathToFileURL(path.resolve(__dirname, '../../../site/index.html')).href

for (const viewport of [
  { width: 390, height: 844 },
  { width: 1440, height: 1000 }
]) {
  test(`landing skip link paints only during keyboard focus at ${viewport.width}px (#193)`, async () => {
    const browser = await chromium.launch()
    try {
      const page = await browser.newPage({ viewport })
      await page.goto(landingPageUrl)

      const skipLink = page.getByRole('link', { name: 'Skip to main content' })
      await expect(skipLink).toHaveCount(1)

      const geometry = async (): Promise<{
        active: boolean
        clipPath: string
        link: { top: number; right: number; bottom: number; left: number }
        header: { top: number; height: number }
        overflow: number
      }> =>
        skipLink.evaluate((node) => {
          const link = node.getBoundingClientRect()
          const header = document.querySelector<HTMLElement>('.site-header')
          if (header === null) throw new Error('site header missing')
          const headerBox = header.getBoundingClientRect()
          return {
            active: document.activeElement === node,
            clipPath: getComputedStyle(node).clipPath,
            link: { top: link.top, right: link.right, bottom: link.bottom, left: link.left },
            header: { top: headerBox.top + scrollY, height: headerBox.height },
            overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth
          }
        })

      const unfocused = await geometry()
      expect(unfocused.active).toBe(false)
      expect(unfocused.clipPath).toBe('inset(50%)')
      expect(unfocused.link.bottom).toBeLessThanOrEqual(0)
      expect(unfocused.overflow).toBeLessThanOrEqual(1)

      await page.keyboard.press('Tab')
      await expect(skipLink).toBeFocused()
      await expect(skipLink).toHaveText('Skip to main content')

      const focused = await geometry()
      expect(focused.active).toBe(true)
      expect(focused.clipPath).toBe('none')
      expect(focused.link.left).toBeGreaterThanOrEqual(0)
      expect(focused.link.top).toBeGreaterThanOrEqual(0)
      expect(focused.link.right).toBeLessThanOrEqual(viewport.width)
      expect(focused.link.bottom).toBeLessThanOrEqual(viewport.height)
      expect(focused.link.right - focused.link.left).toBeGreaterThan(0)
      expect(focused.link.bottom - focused.link.top).toBeGreaterThan(0)
      expect(focused.header).toEqual(unfocused.header)
      expect(focused.overflow).toBeLessThanOrEqual(1)

      await skipLink.press('Enter')
      await expect(page).toHaveURL(/#main$/)
      await page.keyboard.press('Tab')

      const blurred = await geometry()
      expect(blurred.active).toBe(false)
      expect(blurred.clipPath).toBe('inset(50%)')
      expect(blurred.link.bottom).toBeLessThanOrEqual(0)
      expect(blurred.header).toEqual(unfocused.header)
      expect(blurred.overflow).toBeLessThanOrEqual(1)
    } finally {
      await browser.close()
    }
  })
}
