import { createHash } from 'node:crypto'

import type { DiscoveryInfo, EngineState } from '../shared/types'

/**
 * Parsers for the Phase 1 discovery handshake files.
 *
 * - `engine.json` (`DiscoveryInfo`) — written atomically by
 *   `engine/process.py:write_discovery` strictly before the ready sentinel.
 * - `engine-state.json` (`EngineState`) — engine-owned `{port, token}`,
 *   mode 0600, from `engine/settings.py`.
 */

export class DiscoveryParseError extends Error {}

export function parseDiscovery(text: string): DiscoveryInfo {
  const raw = parseObject(text, 'discovery file')
  const { port, pid, token_fingerprint, version } = raw
  if (!Number.isInteger(port)) throw new DiscoveryParseError('discovery file: bad port')
  // Strictly positive: pid 0 / negative pids address process groups in
  // kill(2), so a corrupt file could otherwise aim the stale-kill at the
  // app's own group. Treat anything else as a corrupt/stale discovery file.
  if (!Number.isInteger(pid) || (pid as number) <= 0) {
    throw new DiscoveryParseError('discovery file: bad pid')
  }
  if (typeof token_fingerprint !== 'string' || token_fingerprint === '') {
    throw new DiscoveryParseError('discovery file: bad token_fingerprint')
  }
  if (typeof version !== 'string' || version === '') {
    throw new DiscoveryParseError('discovery file: bad version')
  }
  return { port: port as number, pid: pid as number, token_fingerprint, version }
}

export function parseEngineState(text: string): EngineState {
  const raw = parseObject(text, 'engine state file')
  const { port, token } = raw
  if (!Number.isInteger(port)) throw new DiscoveryParseError('engine state file: bad port')
  if (typeof token !== 'string' || token === '') {
    throw new DiscoveryParseError('engine state file: bad token')
  }
  return { port: port as number, token }
}

/**
 * Mirror of `engine/settings.py:token_fingerprint`: sha256 hex, first 16
 * chars — the non-reversible identifier published in the discovery file.
 */
export function tokenFingerprint(token: string): string {
  return createHash('sha256').update(token, 'utf8').digest('hex').slice(0, 16)
}

function parseObject(text: string, label: string): Record<string, unknown> {
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch (err) {
    throw new DiscoveryParseError(`${label}: invalid JSON (${String(err)})`)
  }
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    throw new DiscoveryParseError(`${label}: not a JSON object`)
  }
  return parsed as Record<string, unknown>
}
