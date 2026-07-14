import { describe, expect, it } from 'vitest'

import { resolveEngineCommand, resolveGuardianCommand, splitEngineCmd } from './engine-cmd'

describe('splitEngineCmd', () => {
  it('splits on whitespace runs (per P6: documented whitespace split)', () => {
    expect(splitEngineCmd('uv run podcast-reader serve')).toEqual([
      'uv',
      'run',
      'podcast-reader',
      'serve'
    ])
  })

  it('collapses repeated whitespace and trims', () => {
    expect(splitEngineCmd('  /opt/engine \t serve  ')).toEqual(['/opt/engine', 'serve'])
  })

  it('returns null for empty or whitespace-only values', () => {
    expect(splitEngineCmd('')).toBeNull()
    expect(splitEngineCmd('   ')).toBeNull()
    expect(splitEngineCmd(undefined)).toBeNull()
  })

  it('does not honor quoting — paths with spaces are unsupported (per P6)', () => {
    expect(splitEngineCmd('"/opt/my engine/bin" serve')).toEqual([
      '"/opt/my',
      'engine/bin"',
      'serve'
    ])
  })
})

describe('resolveEngineCommand', () => {
  const noFile = (): boolean => false

  it('prefers the packaged engine when the resources executable exists', () => {
    const resolved = resolveEngineCommand({
      resourcesPath: '/install/resources',
      env: { PODCAST_READER_ENGINE_CMD: '/elsewhere serve' },
      fileExists: (p) => p === '/install/resources/engine/podcast-reader-engine',
      platform: 'linux'
    })
    expect(resolved).toEqual({
      argv: ['/install/resources/engine/podcast-reader-engine', 'serve'],
      posture: 'packaged'
    })
  })

  it('uses the .exe name on win32', () => {
    const resolved = resolveEngineCommand({
      resourcesPath: 'C:\\app\\resources',
      env: {},
      fileExists: (p) => p.endsWith('podcast-reader-engine.exe'),
      platform: 'win32'
    })
    expect(resolved.posture).toBe('packaged')
    expect(resolved.argv[0]).toContain('podcast-reader-engine.exe')
  })

  it('falls back to PODCAST_READER_ENGINE_CMD, whitespace-split', () => {
    const resolved = resolveEngineCommand({
      resourcesPath: '/install/resources',
      env: { PODCAST_READER_ENGINE_CMD: 'python -m podcast_reader serve' },
      fileExists: noFile,
      platform: 'linux'
    })
    expect(resolved).toEqual({
      argv: ['python', '-m', 'podcast_reader', 'serve'],
      posture: 'override'
    })
  })

  it('falls back to the dev command when nothing else applies', () => {
    const resolved = resolveEngineCommand({
      resourcesPath: null,
      env: {},
      fileExists: noFile,
      platform: 'linux'
    })
    expect(resolved).toEqual({
      argv: ['uv', 'run', 'podcast-reader', 'serve'],
      posture: 'dev'
    })
  })

  it('ignores a whitespace-only override', () => {
    const resolved = resolveEngineCommand({
      resourcesPath: null,
      env: { PODCAST_READER_ENGINE_CMD: '  ' },
      fileExists: noFile,
      platform: 'linux'
    })
    expect(resolved.posture).toBe('dev')
  })
})

describe('resolveGuardianCommand', () => {
  it('uses the same packaged executable with the guardian subcommand', () => {
    expect(
      resolveGuardianCommand({
        resourcesPath: '/install/resources',
        env: {},
        fileExists: () => true,
        platform: 'linux'
      })
    ).toEqual({
      argv: ['/install/resources/engine/podcast-reader-engine', 'serve-guardian'],
      posture: 'packaged'
    })
  })

  it('rejects an override whose subcommand cannot be safely replaced', () => {
    expect(() =>
      resolveGuardianCommand({
        resourcesPath: null,
        env: { PODCAST_READER_ENGINE_CMD: '/custom/wrapper' },
        fileExists: () => false,
        platform: 'linux'
      })
    ).toThrow('must end in "serve"')
  })
})
