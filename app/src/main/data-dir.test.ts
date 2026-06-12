import { describe, expect, it } from 'vitest'

import { DataDirError, resolveDataDir } from './data-dir'

describe('resolveDataDir', () => {
  it('defaults to <home>/PodcastReader without the env override', () => {
    expect(resolveDataDir({}, '/home/u')).toBe('/home/u/PodcastReader')
  })

  it('uses PODCAST_READER_DATA_DIR when set', () => {
    expect(resolveDataDir({ PODCAST_READER_DATA_DIR: '/tmp/eng' }, '/home/u')).toBe('/tmp/eng')
  })

  it('expands a leading ~ in the override like Path.expanduser', () => {
    expect(resolveDataDir({ PODCAST_READER_DATA_DIR: '~/eng' }, '/home/u')).toBe('/home/u/eng')
    expect(resolveDataDir({ PODCAST_READER_DATA_DIR: '~' }, '/home/u')).toBe('/home/u')
  })

  it('treats an empty override as unset (matching the engine truthiness)', () => {
    expect(resolveDataDir({ PODCAST_READER_DATA_DIR: '' }, '/home/u')).toBe('/home/u/PodcastReader')
  })

  it('rejects the ~user form with a clear error instead of mis-resolving it', () => {
    // Python's Path.expanduser would resolve ~bob/eng to bob's home; the app
    // refuses it loudly rather than silently diverging from the engine.
    expect(() => resolveDataDir({ PODCAST_READER_DATA_DIR: '~bob/eng' }, '/home/u')).toThrowError(
      DataDirError
    )
    expect(() => resolveDataDir({ PODCAST_READER_DATA_DIR: '~bob' }, '/home/u')).toThrowError(
      /~user.*not supported/
    )
  })
})
