import { existsSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

/**
 * Committed-asset guard (native-app-first-impression design, v2 icon
 * pipeline). The branded icon source and its rendered PNG are committed; the
 * PNG is what electron-builder derives `.icns`/`.ico` from and what the
 * runtime window loads, so a malformed or wrong-size PNG would silently ship a
 * broken icon. We assert the contract here (`scripts/build-icons.mjs` enforces
 * the same on render); the platform `.icns`/`.ico` are electron-builder's, not
 * ours to validate.
 */

const BUILD_DIR = resolve(__dirname, '..', '..', 'build')
const SVG_PATH = join(BUILD_DIR, 'icon.svg')
const PNG_PATH = join(BUILD_DIR, 'icon.png')
const PNG_MAGIC = Buffer.from([0x89, 0x50, 0x4e, 0x47])

describe('branded icon assets', () => {
  it('commits the SVG source', () => {
    expect(existsSync(SVG_PATH)).toBe(true)
  })

  it('commits a 1024×1024 PNG with valid PNG magic bytes', () => {
    expect(existsSync(PNG_PATH)).toBe(true)
    const bytes = readFileSync(PNG_PATH)
    expect(bytes.subarray(0, 4).equals(PNG_MAGIC)).toBe(true)
    // IHDR width/height are the first two big-endian uint32s of the IHDR data,
    // at byte offsets 16 and 20 after the 8-byte signature + chunk header.
    expect(bytes.readUInt32BE(16)).toBe(1024)
    expect(bytes.readUInt32BE(20)).toBe(1024)
  })
})
