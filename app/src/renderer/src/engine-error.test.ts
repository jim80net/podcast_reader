import { describe, expect, it } from 'vitest'

import { extractEngineDetail, settingsErrorField } from './engine-error'

describe('extractEngineDetail', () => {
  it('strips the ipcRenderer.invoke wrapper and the EngineRequestError prefix', () => {
    const err = new Error(
      "Error invoking remote method 'settings:put': Error: " +
        "engine request failed: 400 unknown chapter provider: 'bogus'"
    )
    expect(extractEngineDetail(err)).toBe("unknown chapter provider: 'bogus'")
  })

  it('returns plain error messages untouched', () => {
    expect(extractEngineDetail(new Error('engine is not ready'))).toBe('engine is not ready')
  })

  it('strips the invoke wrapper even without the engine prefix', () => {
    const err = new Error("Error invoking remote method 'jobs:submit': Error: engine is not ready")
    expect(extractEngineDetail(err)).toBe('engine is not ready')
  })

  it('stringifies non-Error values', () => {
    expect(extractEngineDetail('boom')).toBe('boom')
  })
})

describe('settingsErrorField', () => {
  it('maps custom-URL validation messages to the custom URL field', () => {
    expect(
      settingsErrorField('custom provider base URL must be https, or http on localhost/127.0.0.1')
    ).toBe('custom_provider_url')
    expect(
      settingsErrorField(
        'custom provider requires a base URL (set custom_provider_url / ...)'
      )
    ).toBe('custom_provider_url')
  })

  it('maps unknown-provider messages to the provider field', () => {
    expect(settingsErrorField("unknown chapter provider: 'bogus'")).toBe('chapter_provider')
  })

  it('returns null for anything unrecognized (shown as a general error)', () => {
    expect(settingsErrorField('something else entirely')).toBeNull()
  })
})
