/**
 * Deterministic zip of `dist/` → `podcast-reader-extension.zip` (task 4.1):
 * dependency-free minimal ZIP writer — entries sorted by path, stored
 * (no compression), fixed DOS timestamp — so the same build bytes always
 * produce the same archive bytes. The zip is the load-unpacked alternative
 * and the future Chrome Web Store upload artifact (task 9.2).
 */
import { crc32 } from 'node:zlib'
import { readdirSync, readFileSync, writeFileSync, existsSync } from 'node:fs'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const distDir = join(root, 'dist')
const outFile = join(root, 'podcast-reader-extension.zip')

if (!existsSync(join(distDir, 'manifest.json'))) {
  console.error('dist/manifest.json missing — run the vite build first')
  process.exit(1)
}

// Fixed DOS date/time: 2026-01-01 00:00:00 (determinism over provenance).
const DOS_TIME = 0
const DOS_DATE = ((2026 - 1980) << 9) | (1 << 5) | 1

function walk(dir) {
  const files = []
  for (const entry of readdirSync(dir, { withFileTypes: true }).sort((a, b) =>
    a.name.localeCompare(b.name)
  )) {
    const full = join(dir, entry.name)
    if (entry.isDirectory()) files.push(...walk(full))
    else files.push(full)
  }
  return files
}

const files = walk(distDir)
  .map((full) => relative(distDir, full).split('\\').join('/'))
  .sort()

const locals = []
const centrals = []
let offset = 0

for (const path of files) {
  const data = readFileSync(join(distDir, path))
  const name = Buffer.from(path, 'utf8')
  const crc = crc32(data) >>> 0

  const local = Buffer.alloc(30)
  local.writeUInt32LE(0x04034b50, 0)
  local.writeUInt16LE(20, 4) // version needed
  local.writeUInt16LE(0, 6) // flags
  local.writeUInt16LE(0, 8) // method: stored
  local.writeUInt16LE(DOS_TIME, 10)
  local.writeUInt16LE(DOS_DATE, 12)
  local.writeUInt32LE(crc, 14)
  local.writeUInt32LE(data.length, 18)
  local.writeUInt32LE(data.length, 22)
  local.writeUInt16LE(name.length, 26)
  local.writeUInt16LE(0, 28) // extra length
  locals.push(local, name, data)

  const central = Buffer.alloc(46)
  central.writeUInt32LE(0x02014b50, 0)
  central.writeUInt16LE(20, 4) // version made by
  central.writeUInt16LE(20, 6) // version needed
  central.writeUInt16LE(0, 8)
  central.writeUInt16LE(0, 10) // method: stored
  central.writeUInt16LE(DOS_TIME, 12)
  central.writeUInt16LE(DOS_DATE, 14)
  central.writeUInt32LE(crc, 16)
  central.writeUInt32LE(data.length, 20)
  central.writeUInt32LE(data.length, 24)
  central.writeUInt16LE(name.length, 28)
  central.writeUInt32LE(offset, 42)
  centrals.push(central, name)

  offset += 30 + name.length + data.length
}

const centralStart = offset
const centralBuf = Buffer.concat(centrals)
const end = Buffer.alloc(22)
end.writeUInt32LE(0x06054b50, 0)
end.writeUInt16LE(files.length, 8)
end.writeUInt16LE(files.length, 10)
end.writeUInt32LE(centralBuf.length, 12)
end.writeUInt32LE(centralStart, 16)

writeFileSync(outFile, Buffer.concat([...locals, centralBuf, end]))
console.log(`wrote ${relative(process.cwd(), outFile)} (${files.length} entries)`)
