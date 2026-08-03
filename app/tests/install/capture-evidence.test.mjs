import assert from 'node:assert/strict'
import test from 'node:test'

import { PNG } from 'pngjs'

import { assertPngCapture } from './capture-evidence.mjs'

function pngBytes(width, height, pixel) {
  const image = new PNG({ width, height })
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const offset = (y * width + x) * 4
      const [red, green, blue] = pixel(x, y)
      image.data[offset] = red
      image.data[offset + 1] = green
      image.data[offset + 2] = blue
      image.data[offset + 3] = 255
    }
  }
  return PNG.sync.write(image)
}

test('accepts decoded, correctly sized, non-blank PNG evidence', () => {
  const bytes = pngBytes(4, 3, (x, y) => ((x + y) % 2 === 0 ? [0, 0, 0] : [255, 255, 255]))

  const evidence = assertPngCapture(bytes, {
    label: 'checkerboard',
    expectedWidth: 4,
    expectedHeight: 3
  })

  assert.equal(evidence.width, 4)
  assert.equal(evidence.height, 3)
  assert.ok(evidence.pixelVariance >= 16)
})

test('deliberately blank PNG is rejected', () => {
  const bytes = pngBytes(4, 3, () => [255, 255, 255])

  assert.throws(
    () =>
      assertPngCapture(bytes, {
        label: 'blank control',
        expectedWidth: 4,
        expectedHeight: 3
      }),
    /blank or nearly blank/
  )
})

test('corrupt and wrongly sized captures are rejected', () => {
  assert.throws(
    () =>
      assertPngCapture(Buffer.from('not a png'), {
        label: 'corrupt control',
        expectedWidth: 4,
        expectedHeight: 3
      }),
    /not a decodable PNG/
  )

  const bytes = pngBytes(4, 3, (x, y) => ((x + y) % 2 === 0 ? [0, 0, 0] : [255, 255, 255]))
  assert.throws(
    () =>
      assertPngCapture(bytes, {
        label: 'geometry control',
        expectedWidth: 5,
        expectedHeight: 3
      }),
    /expected 5×3/
  )
})
