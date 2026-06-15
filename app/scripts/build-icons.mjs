#!/usr/bin/env node
/**
 * Icon render step (native-app-first-impression design, v2 icon pipeline).
 *
 *   node scripts/build-icons.mjs
 *
 * Renders the committed source `build/icon.svg` → `build/icon.png` (1024×1024)
 * via `rsvg-convert`, then asserts the output is a real 1024×1024 PNG. This is
 * a DOCUMENTED DEV STEP, not a build/CI dependency: `icon.png` is committed, so
 * a fresh checkout and CI never need rsvg/ImageMagick. electron-builder 26
 * derives the platform `.icns`/`.ico` from `icon.png` at packaging time, so we
 * neither generate nor commit those — they are not our artifacts to validate.
 *
 * Re-run this only when the mark changes (edit `icon.svg`, then regenerate) or
 * a designer drops in a replacement 1024px source.
 */
import { spawnSync } from 'node:child_process'
import { readFileSync, statSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const BUILD_DIR = resolve(dirname(fileURLToPath(import.meta.url)), '..', 'build')
const SVG_PATH = join(BUILD_DIR, 'icon.svg')
const PNG_PATH = join(BUILD_DIR, 'icon.png')
const SIZE = 1024
const PNG_MAGIC = Buffer.from([0x89, 0x50, 0x4e, 0x47]) // \x89 P N G

/** Big-endian uint32 from a 4-byte slice. */
function readUint32BE(buffer, offset) {
  return buffer.readUInt32BE(offset)
}

/** Assert `build/icon.png` is a PNG of exactly SIZE×SIZE; throw otherwise. */
function assertPng() {
  const bytes = readFileSync(PNG_PATH)
  if (!bytes.subarray(0, 4).equals(PNG_MAGIC)) {
    throw new Error(`${PNG_PATH} is not a PNG (bad magic bytes)`)
  }
  // PNG: 8-byte signature, then the IHDR chunk (length+type+data); width and
  // height are the first two big-endian uint32s of the IHDR data at offsets
  // 16 and 20.
  const width = readUint32BE(bytes, 16)
  const height = readUint32BE(bytes, 20)
  if (width !== SIZE || height !== SIZE) {
    throw new Error(`${PNG_PATH} is ${width}×${height}, expected ${SIZE}×${SIZE}`)
  }
  return { bytes: bytes.length, width, height }
}

function main() {
  if (!statSync(SVG_PATH, { throwIfNoEntry: false })) {
    throw new Error(`source icon missing: ${SVG_PATH}`)
  }
  const result = spawnSync(
    'rsvg-convert',
    ['--width', String(SIZE), '--height', String(SIZE), '--output', PNG_PATH, SVG_PATH],
    { stdio: ['ignore', 'inherit', 'inherit'] }
  )
  if (result.error !== undefined) {
    const code = /** @type {NodeJS.ErrnoException} */ (result.error).code
    if (code === 'ENOENT') {
      throw new Error(
        'rsvg-convert not found. Install librsvg (e.g. `brew install librsvg` or ' +
          '`apt-get install librsvg2-bin`). This is a dev-only tool; CI never needs it.'
      )
    }
    throw result.error
  }
  if (result.status !== 0) {
    throw new Error(`rsvg-convert exited with status ${String(result.status)}`)
  }
  const info = assertPng()
  console.log(
    `built ${PNG_PATH} (${info.width}×${info.height}, ${info.bytes} bytes) from ${SVG_PATH}`
  )
}

main()
