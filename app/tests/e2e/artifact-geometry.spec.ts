import { chromium, expect, test } from '@playwright/test'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

/**
 * Anchor-offset regression gate for the transcript artifact (issue #63).
 *
 * A fixed scroll-padding cannot track the variable-height sticky jump rail
 * (#50 shipped the defect at 1.5rem, #60's wrap re-introduced it at 4rem).
 * The artifact now measures the rail and sets scroll-padding itself; this
 * spec loads the committed longform golden (kept current by pytest's
 * byte-compare — see tests/regen_goldens.py at the repo root) in a plain
 * chromium page and asserts, at both walk widths:
 *
 *   1. effective scroll-padding-top >= the rail's live height, and
 *   2. after an anchor jump settles, the target's top edge sits at or
 *      below the rail's bottom edge — never under it.
 *
 * No Electron, no engine: this is renderer-output geometry only.
 */

const longformArtifactUrl = pathToFileURL(
  path.resolve(__dirname, '../../../tests/fixtures/sample_expected_longform.html')
).href
const nearHourArtifactUrl = pathToFileURL(
  path.resolve(__dirname, '../../../tests/fixtures/sample_expected_near_hour.html')
).href
const chapteredArtifactUrl = pathToFileURL(
  path.resolve(__dirname, '../../../tests/fixtures/sample_expected.html')
).href

for (const viewport of [
  { width: 390, height: 844 },
  { width: 1280, height: 900 }
]) {
  for (const theme of ['dark', 'light'] as const) {
    test(`full reader stays usable at ${viewport.width}px in ${theme} theme (#81)`, async ({}, testInfo) => {
      const browser = await chromium.launch()
      try {
        const page = await browser.newPage({ viewport })
        await page.goto(chapteredArtifactUrl)
        await page.evaluate((selectedTheme) => {
          document.documentElement.dataset.theme = selectedTheme
        }, theme)

        const layout = await page.evaluate(() => {
          const content = document.querySelector('#content')
          const heading = document.querySelector('h1')
          const paragraph = document.querySelector('p[data-start]')
          if (content === null || heading === null || paragraph === null) {
            throw new Error('chaptered artifact lacks reader landmarks')
          }
          return {
            viewportWidth: document.documentElement.clientWidth,
            documentWidth: document.documentElement.scrollWidth,
            contentWidth: content.getBoundingClientRect().width,
            headingHeight: heading.getBoundingClientRect().height,
            paragraphHeight: paragraph.getBoundingClientRect().height
          }
        })
        expect(layout.documentWidth).toBeLessThanOrEqual(layout.viewportWidth + 1)
        expect(layout.contentWidth).toBeGreaterThan(0)
        expect(layout.headingHeight).toBeGreaterThan(0)
        expect(layout.paragraphHeight).toBeGreaterThan(0)

        const screenshotName = `full-reader-${viewport.width}-${theme}.png`
        const screenshotPath = testInfo.outputPath(screenshotName)
        await page.screenshot({ path: screenshotPath, fullPage: true })
        await testInfo.attach(screenshotName, {
          path: screenshotPath,
          contentType: 'image/png'
        })
      } finally {
        await browser.close()
      }
    })
  }
}

interface GeometryProbe {
  pad: number
  railHeight: number
  railBottom: number
  targetTop: number
  stuck: boolean
}

for (const viewport of [
  { width: 390, height: 844 },
  { width: 1280, height: 900 }
]) {
  test(`anchor jumps clear the rail at ${viewport.width}px (#63)`, async () => {
    const browser = await chromium.launch()
    try {
      const page = await browser.newPage({ viewport })
      await page.goto(longformArtifactUrl)

      const probe = await page.evaluate(
        () =>
          new Promise<GeometryProbe>((resolve, reject) => {
            const rail = document.querySelector('.timeline-nav')
            const anchors = document.querySelectorAll('p[id^="t-"]')
            if (rail === null || anchors.length === 0) {
              reject(new Error('longform golden lacks a rail or anchors'))
              return
            }
            const target = anchors[Math.floor(anchors.length / 2)]
            if (target === undefined) {
              reject(new Error('no mid-document anchor'))
              return
            }
            location.hash = `#${target.id}`
            // Smooth scrolling: poll until the scroll position is stable
            // across frames instead of guessing a fixed delay.
            let last = -1
            let stableFrames = 0
            const started = performance.now()
            const settle = (): void => {
              if (window.scrollY === last) {
                stableFrames += 1
              } else {
                stableFrames = 0
                last = window.scrollY
              }
              if (stableFrames >= 10 || performance.now() - started > 5000) {
                resolve({
                  pad: parseFloat(getComputedStyle(document.documentElement).scrollPaddingTop),
                  railHeight: rail.getBoundingClientRect().height,
                  railBottom: rail.getBoundingClientRect().bottom,
                  targetTop: target.getBoundingClientRect().top,
                  stuck: rail.classList.contains('stuck')
                })
                return
              }
              requestAnimationFrame(settle)
            }
            requestAnimationFrame(settle)
          })
      )

      // The regression assertion #63 names: offset >= rail height.
      expect(probe.pad).toBeGreaterThanOrEqual(probe.railHeight)
      // And the user-visible truth: the jumped-to passage is not under the rail.
      expect(probe.targetTop).toBeGreaterThanOrEqual(probe.railBottom - 1)
      // Mid-scroll the rail must be in its collapsed state.
      expect(probe.stuck).toBe(true)
    } finally {
      await browser.close()
    }
  })
}

interface DensityProbe {
  linkCount: number
  rowCount: number
  railHeight: number
  allLinksVisible: boolean
}

for (const viewport of [
  { width: 390, height: 844, maxRows: 6 },
  { width: 1280, height: 900, maxRows: 3 }
]) {
  for (const theme of ['dark', 'light'] as const) {
    test(`near-hour rail stays compact at ${viewport.width}px in ${theme} theme (#72)`, async ({}, testInfo) => {
      const browser = await chromium.launch()
      try {
        const page = await browser.newPage({ viewport })
        await page.goto(nearHourArtifactUrl)
        await page.evaluate((selectedTheme) => {
          document.documentElement.dataset.theme = selectedTheme
        }, theme)

        const probe = await page.evaluate((): DensityProbe => {
          const rail = document.querySelector<HTMLElement>('.timeline-nav')
          const links = Array.from(document.querySelectorAll<HTMLElement>('.timeline-links a'))
          if (rail === null) throw new Error('near-hour golden lacks a timeline rail')
          const railRect = rail.getBoundingClientRect()
          const linkRects = links.map((link) => link.getBoundingClientRect())
          const rows = new Set(linkRects.map((rect) => Math.round(rect.top)))
          return {
            linkCount: links.length,
            rowCount: rows.size,
            railHeight: railRect.height,
            allLinksVisible: links.every((link, index) => {
              const rect = linkRects[index]
              const style = getComputedStyle(link)
              return (
                rect !== undefined &&
                style.display !== 'none' &&
                style.visibility !== 'hidden' &&
                rect.width > 0 &&
                rect.height > 0 &&
                rect.left >= railRect.left - 1 &&
                rect.right <= railRect.right + 1 &&
                rect.top >= railRect.top - 1 &&
                rect.bottom <= railRect.bottom + 1
              )
            })
          }
        })

        expect(probe.linkCount).toBe(6)
        expect(probe.rowCount).toBeLessThanOrEqual(viewport.maxRows)
        expect(probe.railHeight).toBeLessThan(viewport.height * 0.25)
        expect(probe.allLinksVisible).toBe(true)
        const screenshotName = `near-hour-rail-${viewport.width}-${theme}.png`
        const screenshotPath = testInfo.outputPath(screenshotName)
        await page.locator('.timeline-nav').screenshot({ path: screenshotPath })
        await testInfo.attach(screenshotName, {
          path: screenshotPath,
          contentType: 'image/png'
        })
      } finally {
        await browser.close()
      }
    })
  }
}

const badgeColors = {
  dark: { background: 'rgb(49, 70, 90)', foreground: 'rgb(242, 246, 248)' },
  light: { background: 'rgb(216, 230, 237)', foreground: 'rgb(41, 73, 90)' }
} as const

for (const theme of ['dark', 'light'] as const) {
  test(`section badges use the reviewed ${theme} palette in both placements (#73)`, async ({}, testInfo) => {
    const browser = await chromium.launch()
    try {
      const page = await browser.newPage({ viewport: { width: 1280, height: 900 } })
      await page.goto(chapteredArtifactUrl)
      await page.evaluate((selectedTheme) => {
        document.documentElement.dataset.theme = selectedTheme
      }, theme)

      for (const selector of [
        '.nav-badge-intro',
        '.nav-badge-outro',
        '.badge-intro',
        '.badge-outro'
      ]) {
        const colors = await page.locator(selector).evaluate((badge) => {
          const style = getComputedStyle(badge)
          return { background: style.backgroundColor, foreground: style.color }
        })
        expect(colors).toEqual(badgeColors[theme])
      }

      const screenshotName = `section-badges-1280-${theme}.png`
      const screenshotPath = testInfo.outputPath(screenshotName)
      await page.screenshot({ path: screenshotPath })
      await testInfo.attach(screenshotName, {
        path: screenshotPath,
        contentType: 'image/png'
      })
    } finally {
      await browser.close()
    }
  })
}
