import type { Pairing } from './storage'

/**
 * Pairing-input parsing and the claim flow (ext-pairing spec): the popup
 * accepts the combined `<port>-<code>` paste string as the primary input
 * (matching the app's Settings display, per review adjudication) with
 * separate port/code fields as fallback; the flow is claim → authed health
 * verify → store, and any failure leaves a previously stored pairing
 * untouched (callers persist only an `ok` result).
 */

/** Mirror of engine/pairing.py CODE_ALPHABET (Crockford-style, no 0/O/1/I/L/U). */
export const CODE_ALPHABET = '23456789ABCDEFGHJKMNPQRSTVWXYZ'
export const CODE_LENGTH = 6

export interface PairingInput {
  port: number
  code: string
}

/** Parse the combined `<port>-<code>` paste string; null when malformed. */
export function parseCombined(raw: string): PairingInput | null {
  const match = /^(\d{1,5})-([A-Za-z0-9]{6})$/.exec(raw.trim())
  if (match === null) return null
  return buildInput(match[1] ?? '', match[2] ?? '')
}

/** Parse the separate port and code fallback fields; null when malformed. */
export function parseFields(portRaw: string, codeRaw: string): PairingInput | null {
  if (!/^\d{1,5}$/.test(portRaw.trim())) return null
  return buildInput(portRaw.trim(), codeRaw.trim())
}

/**
 * Resolve whichever input the user provided: a non-empty combined string
 * wins; otherwise the separate fields. Null when neither parses.
 */
export function resolvePairingInput(
  combined: string,
  portRaw: string,
  codeRaw: string
): PairingInput | null {
  if (combined.trim() !== '') return parseCombined(combined)
  return parseFields(portRaw, codeRaw)
}

export type PairFailureReason = 'unreachable' | 'rejected' | 'verify-failed'

export type PairResult = { ok: true; pairing: Pairing } | { ok: false; reason: PairFailureReason }

export interface PairingDeps {
  /** `POST /v1/pair/claim` — resolves the token, throws { status } on HTTP failure. */
  claim(port: number, code: string): Promise<string>
  /** Authed `GET /v1/health` with the candidate pairing — resolves when valid. */
  verify(pairing: Pairing): Promise<unknown>
}

/**
 * The claim flow state machine: claim the code, verify the received token
 * with an authed health probe, and only then hand back a storable pairing.
 * Failure classification drives the popup's self-authored error copy.
 */
export async function performPairing(input: PairingInput, deps: PairingDeps): Promise<PairResult> {
  let token: string
  try {
    token = await deps.claim(input.port, input.code)
  } catch (err) {
    return { ok: false, reason: isHttpError(err) ? 'rejected' : 'unreachable' }
  }
  const pairing: Pairing = { port: input.port, token }
  try {
    await deps.verify(pairing)
  } catch {
    return { ok: false, reason: 'verify-failed' }
  }
  return { ok: true, pairing }
}

function buildInput(portRaw: string, codeRaw: string): PairingInput | null {
  const port = Number(portRaw)
  if (!Number.isInteger(port) || port < 1 || port > 65535) return null
  const code = codeRaw.toUpperCase()
  if (code.length !== CODE_LENGTH) return null
  for (const char of code) {
    if (!CODE_ALPHABET.includes(char)) return null
  }
  return { port, code }
}

function isHttpError(err: unknown): boolean {
  return typeof err === 'object' && err !== null && typeof (err as { status?: unknown }).status === 'number'
}
