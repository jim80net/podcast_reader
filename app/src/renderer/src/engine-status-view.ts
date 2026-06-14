import type { EngineStatus } from '../../shared/ipc'

/**
 * Pure mapping from the supervision `EngineStatus` union to the renderer's
 * engine-pill / error-banner view model. Extracted from main.ts so the
 * status → text mapping is unit-testable and so the `assertNever` default
 * makes a future EngineStatus member a compile error rather than a silently
 * blank pill (engine-respawn-supervision design, H1).
 */

export interface EngineStatusView {
  /** Short text for the always-visible engine pill. */
  pill: string
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
      return { pill: 'engine starting…', banner: null, showRestart: false }
    case 'ready':
      return {
        pill: `engine v${status.version}${status.adopted ? ' (adopted)' : ''}`,
        banner: null,
        showRestart: false
      }
    case 'restarting':
      return {
        pill: 'engine restarting…',
        banner: `Reconnecting to engine… (attempt ${status.attempt}/${status.maxAttempts})`,
        showRestart: false
      }
    case 'failed':
      return {
        pill: 'engine failed',
        banner: `Engine failed to start: ${status.message}`,
        showRestart: true
      }
    case 'stopped':
      return { pill: 'engine stopped', banner: null, showRestart: false }
    default:
      return assertNever(status)
  }
}
