import type { EngineStatus } from '../../shared/ipc'

/**
 * Pure mapping from the supervision `EngineStatus` union to the renderer's
 * engine-pill / error-banner view model. Extracted from main.ts so the
 * status → text mapping is unit-testable and so the `assertNever` default
 * makes a future EngineStatus member a compile error rather than a silently
 * blank pill (engine-respawn-supervision design, H1).
 */

export interface EngineStatusView {
  /** Short text for the diagnostic engine pill. */
  pill: string
  /** Healthy readiness is deliberately quiet in the normal app shell. */
  showPill: boolean
  /** Banner text, or null to hide the banner. */
  banner: string | null
  /** Whether to offer the manual "Restart engine" button (terminal failure). */
  showRestart: boolean
}

/** Compile-time exhaustiveness guard: an unhandled union member fails the build. */
export function assertNever(value: never): never {
  throw new Error(`unhandled case: ${JSON.stringify(value)}`)
}

export function engineStatusView(status: EngineStatus): EngineStatusView {
  switch (status.state) {
    case 'starting':
      return { pill: 'Starting…', showPill: true, banner: null, showRestart: false }
    case 'ready':
      return {
        pill: `Ready${status.adopted ? ' (adopted)' : ''}`,
        showPill: false,
        banner: null,
        showRestart: false
      }
    case 'restarting':
      return {
        pill: 'Reconnecting…',
        showPill: true,
        banner: `Reconnecting to engine… (attempt ${status.attempt}/${status.maxAttempts})`,
        showRestart: false
      }
    case 'failed':
      return {
        pill: 'Engine unavailable',
        showPill: true,
        banner: `Engine failed to start: ${status.message}`,
        showRestart: true
      }
    case 'stopped':
      return { pill: 'Engine stopped', showPill: true, banner: null, showRestart: false }
    default:
      return assertNever(status)
  }
}
