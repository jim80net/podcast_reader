import { describe, expect, it } from 'vitest'

import { resolveDataDir } from './data-dir'

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
})
