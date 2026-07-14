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

const artifactUrl = pathToFileURL(
  path.resolve(__dirname, '../../../tests/fixtures/sample_expected_longform.html')
).href

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
      await page.goto(artifactUrl)

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
