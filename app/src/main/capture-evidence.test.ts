import { describe, expect, it } from 'vitest'

import { assertPngCapture } from '../../tests/install/capture-evidence.mjs'

const CHECKERBOARD = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAQAAAADCAYAAAC09K7GAAAAGklEQVR4AWMAgv8gwMDA8B8EmBjQABMDGgAAmQ4J+3xLn44AAAAASUVORK5CYII=',
  'base64'
)
const BLANK = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAQAAAADCAYAAAC09K7GAAAAFUlEQVR4AWP8DwQMSICJAQ0wMaABAMIKBAISoysKAAAAAElFTkSuQmCC',
  'base64'
)

describe('captured screenshot evidence', () => {
  it('accepts a decoded, correctly sized, non-blank PNG', () => {
    const evidence = assertPngCapture(CHECKERBOARD, {
      label: 'checkerboard',
      expectedWidth: 4,
      expectedHeight: 3
    })

    expect(evidence.width).toBe(4)
    expect(evidence.height).toBe(3)
    expect(evidence.pixelVariance).toBeGreaterThanOrEqual(16)
  })

  it('rejects the deliberately blank negative control', () => {
    expect(() =>
      assertPngCapture(BLANK, {
        label: 'blank control',
        expectedWidth: 4,
        expectedHeight: 3
      })
    ).toThrow(/blank or nearly blank/)
  })

  it('rejects corrupt and wrongly sized captures', () => {
    expect(() =>
      assertPngCapture(Buffer.from('not a png'), {
        label: 'corrupt control',
        expectedWidth: 4,
        expectedHeight: 3
      })
    ).toThrow(/not a decodable PNG/)

    expect(() =>
      assertPngCapture(CHECKERBOARD, {
        label: 'geometry control',
        expectedWidth: 5,
        expectedHeight: 3
      })
    ).toThrow(/expected 5×3/)
  })
})
