import { writeFile } from 'node:fs/promises'

import { PNG } from 'pngjs'

const MIN_PIXEL_VARIANCE = 16

function errorMessage(error) {
  return error instanceof Error ? error.message : String(error)
}

/**
 * Decode a PNG and prove it has the expected physical dimensions and enough
 * luminance variance to be rendered evidence rather than a blank frame.
 */
export function assertPngCapture(
  bytes,
  { label = 'capture', expectedWidth, expectedHeight, minimumVariance = MIN_PIXEL_VARIANCE }
) {
  let image
  try {
    image = PNG.sync.read(bytes, { checkCRC: true })
  } catch (error) {
    throw new Error(`${label} is not a decodable PNG: ${errorMessage(error)}`, { cause: error })
  }

  if (image.width !== expectedWidth || image.height !== expectedHeight) {
    throw new Error(
      `${label} is ${image.width}×${image.height}, expected ${expectedWidth}×${expectedHeight}`
    )
  }

  const pixels = image.width * image.height
  let luminanceSum = 0
  let luminanceSquaredSum = 0
  for (let offset = 0; offset < image.data.length; offset += 4) {
    const luminance =
      (77 * image.data[offset] + 150 * image.data[offset + 1] + 29 * image.data[offset + 2]) /
      256
    luminanceSum += luminance
    luminanceSquaredSum += luminance * luminance
  }
  const mean = luminanceSum / pixels
  const variance = Math.max(0, luminanceSquaredSum / pixels - mean * mean)
  if (!Number.isFinite(variance) || variance < minimumVariance) {
    throw new Error(
      `${label} is blank or nearly blank (pixel variance ${variance.toFixed(2)}; ` +
        `minimum ${minimumVariance.toFixed(2)})`
    )
  }

  return { width: image.width, height: image.height, pixelVariance: variance }
}

/** Capture a Playwright page and validate the resulting rendered-pixel evidence. */
export async function capturePageEvidence(
  page,
  { path, fullPage = false, caret = 'hide', label = path }
) {
  const metrics = await page.evaluate((captureFullPage) => {
    const root = document.documentElement
    const body = document.body
    const cssWidth = captureFullPage
      ? Math.max(root.clientWidth, root.scrollWidth, body?.clientWidth ?? 0, body?.scrollWidth ?? 0)
      : window.innerWidth
    const cssHeight = captureFullPage
      ? Math.max(root.clientHeight, root.scrollHeight, body?.clientHeight ?? 0, body?.scrollHeight ?? 0)
      : window.innerHeight
    return {
      width: Math.round(cssWidth * window.devicePixelRatio),
      height: Math.round(cssHeight * window.devicePixelRatio)
    }
  }, fullPage)
  const bytes = await page.screenshot({ path, fullPage, caret, scale: 'device' })
  return assertPngCapture(bytes, {
    label,
    expectedWidth: metrics.width,
    expectedHeight: metrics.height
  })
}

/**
 * Capture an attached Electron page at an explicit physical raster scale.
 *
 * Playwright does not know about device metrics applied after attaching to an
 * Electron window, so its `scale: 'device'` screenshot path still uses the
 * context's original scale of 1. A raw CDP compositor capture honors the
 * renderer DPR; its clip scale stays at 1 to avoid applying that DPR twice.
 */
export async function captureScaledPageEvidence(
  page,
  cdp,
  { path, deviceScaleFactor, fullPage = false, caret = 'hide', label = path }
) {
  const metrics = await page.evaluate((captureFullPage) => {
    const root = document.documentElement
    const body = document.body
    return {
      cssWidth: captureFullPage
        ? Math.max(root.clientWidth, root.scrollWidth, body?.clientWidth ?? 0, body?.scrollWidth ?? 0)
        : window.innerWidth,
      cssHeight: captureFullPage
        ? Math.max(root.clientHeight, root.scrollHeight, body?.clientHeight ?? 0, body?.scrollHeight ?? 0)
        : window.innerHeight,
      devicePixelRatio: window.devicePixelRatio
    }
  }, fullPage)
  if (Math.abs(metrics.devicePixelRatio - deviceScaleFactor) >= 0.01) {
    throw new Error(
      `${label} renderer DPR is ${metrics.devicePixelRatio}, expected ${deviceScaleFactor}`
    )
  }

  const caretStyle =
    caret === 'hide'
      ? await page.addStyleTag({ content: '*, *::before, *::after { caret-color: transparent !important; }' })
      : undefined
  let response
  try {
    response = await cdp.send('Page.captureScreenshot', {
      format: 'png',
      fromSurface: true,
      captureBeyondViewport: fullPage,
      clip: {
        x: 0,
        y: 0,
        width: metrics.cssWidth,
        height: metrics.cssHeight,
        scale: 1
      }
    })
  } finally {
    if (caretStyle !== undefined) {
      await caretStyle.evaluate((style) => style.remove()).catch(() => undefined)
    }
  }

  const bytes = Buffer.from(response.data, 'base64')
  const evidence = assertPngCapture(bytes, {
    label,
    expectedWidth: Math.round(metrics.cssWidth * deviceScaleFactor),
    expectedHeight: Math.round(metrics.cssHeight * deviceScaleFactor)
  })
  await writeFile(path, bytes)
  return { ...evidence, devicePixelRatio: metrics.devicePixelRatio }
}

/** Capture a Playwright locator and validate its exact physical crop. */
export async function captureLocatorEvidence(locator, { path, label = path }) {
  const box = await locator.boundingBox()
  if (box === null) throw new Error(`${label} target has no visible bounding box`)
  const devicePixelRatio = await locator.evaluate(() => window.devicePixelRatio)
  const expectedWidth =
    Math.ceil((box.x + box.width) * devicePixelRatio) - Math.floor(box.x * devicePixelRatio)
  const expectedHeight =
    Math.ceil((box.y + box.height) * devicePixelRatio) - Math.floor(box.y * devicePixelRatio)
  const bytes = await locator.screenshot({ path, scale: 'device' })
  return assertPngCapture(bytes, { label, expectedWidth, expectedHeight })
}
