import { describe, expect, it } from 'vitest'

import { mediaToggleText } from './views/reader'

describe('Reader media toggle copy', () => {
  it('names audio controls as audio', () => {
    expect(mediaToggleText('audio', false)).toBe('▾ Hide audio')
    expect(mediaToggleText('audio', true)).toBe('▸ Show audio')
  })

  it('names visual media controls as video', () => {
    expect(mediaToggleText('video', false)).toBe('▾ Hide video')
    expect(mediaToggleText('youtube', true)).toBe('▸ Show video')
  })
})
