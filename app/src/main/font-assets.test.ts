import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'

import { describe, expect, it } from 'vitest'

interface FontProvenance {
  runtime: string
  fonts: Array<{
    family: string
    license: string
    license_file: string
    license_sha256: string
    member: { shipped_as: string; size: number; sha256: string }
  }>
}

const renderer = new URL('../renderer/', import.meta.url)
const provenancePath = new URL('public/fonts/provenance.json', renderer)

function sha256(bytes: Buffer): string {
  return createHash('sha256').update(bytes).digest('hex')
}

describe('bundled renderer fonts', () => {
  it('matches every shipped font and license to its pinned provenance', () => {
    const provenance = JSON.parse(readFileSync(provenancePath, 'utf8')) as FontProvenance

    expect(provenance.runtime).toContain('no remote font requests')
    expect(provenance.fonts.map(({ family }) => family)).toEqual([
      'Source Serif 4',
      'Source Sans 3'
    ])

    for (const font of provenance.fonts) {
      expect(font.license).toBe('OFL-1.1')

      const fontBytes = readFileSync(new URL(`src/assets/fonts/${font.member.shipped_as}`, renderer))
      expect(fontBytes).toHaveLength(font.member.size)
      expect(sha256(fontBytes)).toBe(font.member.sha256)

      const licenseBytes = readFileSync(new URL(`public/fonts/${font.license_file}`, renderer))
      expect(sha256(licenseBytes)).toBe(font.license_sha256)
      expect(licenseBytes.toString('utf8')).toContain('SIL OPEN FONT LICENSE Version 1.1')
    }
  })
})
