/**
 * Pure helpers for the Settings extension-pairing and cookie sections
 * (app-views spec, design decision 11): the combined `<port>-<code>` paste
 * string (the primary pairing affordance, per review adjudication), the
 * expiry countdown math, and capture-date formatting for the cookie-jar
 * list. All render-side; nothing here touches IPC.
 */

/** The copy-pasteable pairing string the extension popup accepts verbatim. */
export function combinedPairingString(port: number, code: string): string {
  return `${port}-${code}`
}

export interface PairingCountdown {
  expired: boolean
  /** `M:SS` remaining while live; a re-mint prompt once expired. */
  label: string
}

/** Countdown toward `expires_at` (epoch seconds, engine clock) at `nowMs`. */
export function pairingCountdown(expiresAt: number, nowMs: number): PairingCountdown {
  const remainingS = Math.floor(expiresAt - nowMs / 1000)
  if (remainingS <= 0) {
    return { expired: true, label: 'Code expired — mint a new one.' }
  }
  const minutes = Math.floor(remainingS / 60)
  const seconds = remainingS % 60
  return { expired: false, label: `Expires in ${minutes}:${String(seconds).padStart(2, '0')}` }
}

/** Deterministic (locale-independent) capture date for a jar's `created_at`. */
export function formatCaptureDate(createdAtS: number): string {
  return new Date(createdAtS * 1000).toISOString().slice(0, 10)
}
