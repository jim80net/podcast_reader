import { EngineRequestError } from './client'
import type { Pairing } from './storage'

/**
 * Popup-open connection probe (ext-pairing spec, reconnection requirement):
 * the stored `{port, token}` is durable because the engine port is fixed per
 * install. A connection failure means "the desktop app isn't running"
 * (launch affordance, pairing kept); a 401 means the token rotated
 * (re-pair flow replaces the stored pairing). No port scanning, no token
 * recovery beyond re-running pairing.
 */

export type ConnectionState =
  | { state: 'unpaired' }
  | { state: 'connected'; pairing: Pairing }
  | { state: 'engine-down'; pairing: Pairing }
  | { state: 'unauthorized'; pairing: Pairing }

export async function probeConnection(
  pairing: Pairing | null,
  health: (pairing: Pairing) => Promise<unknown>
): Promise<ConnectionState> {
  if (pairing === null) return { state: 'unpaired' }
  try {
    await health(pairing)
    return { state: 'connected', pairing }
  } catch (err) {
    if (err instanceof EngineRequestError && err.status === 401) {
      return { state: 'unauthorized', pairing }
    }
    return { state: 'engine-down', pairing }
  }
}
