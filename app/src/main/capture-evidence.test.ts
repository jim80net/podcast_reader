import { mkdtemp, readFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { describe, expect, it, vi } from 'vitest'

import {
  assertPngCapture,
  captureScaledPageEvidence
} from '../../tests/install/capture-evidence.mjs'

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

  it('rasterizes attached Electron pages at the requested physical scale', async () => {
    const outputDir = await mkdtemp(join(tmpdir(), 'podcast-reader-capture-'))
    const outputPath = join(outputDir, 'scaled.png')
    const bytes = Buffer.from(
      'iVBORw0KGgoAAAANSUhEUgAAAAUAAAAECAYAAABGM/VAAAAAIUlEQVR4ARXBAQEAAAjDIPqXnh68Bg3SQ4PGa9AgPTRoDt7XMc/8SjA5AAAAAElFTkSuQmCC',
      'base64'
    )
    const remove = vi.fn()
    const page = {
      evaluate: vi.fn().mockResolvedValue({ cssWidth: 4, cssHeight: 3, devicePixelRatio: 1.25 }),
      addStyleTag: vi.fn().mockResolvedValue({
        evaluate: async (callback: (style: { remove(): void }) => void) => callback({ remove })
      })
    }
    const cdp = { send: vi.fn().mockResolvedValue({ data: bytes.toString('base64') }) }

    try {
      const evidence = await captureScaledPageEvidence(page as never, cdp as never, {
        path: outputPath,
        deviceScaleFactor: 1.25,
        label: 'scaled control'
      })

      expect(cdp.send).toHaveBeenCalledWith('Page.captureScreenshot', {
        format: 'png',
        fromSurface: true,
        captureBeyondViewport: false,
        clip: { x: 0, y: 0, width: 4, height: 3, scale: 1 }
      })
      expect(evidence).toMatchObject({ width: 5, height: 4, devicePixelRatio: 1.25 })
      expect(await readFile(outputPath)).toEqual(bytes)
      expect(remove).toHaveBeenCalledOnce()
    } finally {
      await rm(outputDir, { recursive: true, force: true })
    }
  })

  it('rejects mismatched renderer scale before taking a screenshot', async () => {
    const page = {
      evaluate: vi.fn().mockResolvedValue({ cssWidth: 4, cssHeight: 3, devicePixelRatio: 1 })
    }
    const cdp = { send: vi.fn() }

    await expect(
      captureScaledPageEvidence(page as never, cdp as never, {
        path: 'unused.png',
        deviceScaleFactor: 1.25,
        label: 'scale control'
      })
    ).rejects.toThrow(/renderer DPR is 1, expected 1.25/)
    expect(cdp.send).not.toHaveBeenCalled()
  })
})
