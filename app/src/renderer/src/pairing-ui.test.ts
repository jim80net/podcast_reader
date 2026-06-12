import { describe, expect, it } from 'vitest'

import { combinedPairingString, formatCaptureDate, pairingCountdown } from './pairing-ui'

describe('combinedPairingString', () => {
  it('joins port and code with a hyphen (the popup parse format)', () => {
    expect(combinedPairingString(51234, 'ABC234')).toBe('51234-ABC234')
  })
})

describe('pairingCountdown', () => {
  it('formats remaining minutes and zero-padded seconds', () => {
    expect(pairingCountdown(1000 + 299, 1000_000)).toEqual({
      expired: false,
      label: 'Expires in 4:59'
    })
    expect(pairingCountdown(1000 + 60, 1000_000)).toEqual({
      expired: false,
      label: 'Expires in 1:00'
    })
    expect(pairingCountdown(1000 + 9, 1000_000)).toEqual({
      expired: false,
      label: 'Expires in 0:09'
    })
  })

  it('reports expiry at and after the deadline', () => {
    expect(pairingCountdown(1000, 1000_000).expired).toBe(true)
    expect(pairingCountdown(995, 1000_000).expired).toBe(true)
    expect(pairingCountdown(1000, 1000_000).label).toMatch(/expired/i)
  })

  it('treats a sub-second remainder as expired rather than showing 0:00', () => {
    expect(pairingCountdown(1000.4, 1000_000).expired).toBe(true)
  })
})

describe('formatCaptureDate', () => {
  it('renders the UTC date of the epoch-seconds timestamp', () => {
    expect(formatCaptureDate(0)).toBe('1970-01-01')
    expect(formatCaptureDate(1767225600)).toBe('2026-01-01')
  })
})
