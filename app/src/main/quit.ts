/**
 * The quit sequence (design decision 3), as a pure state machine over
 * injected operations so it is unit-testable without processes:
 *
 *   abort SSE (per P1) → POST /v1/shutdown → bounded wait for engine exit
 *   → force-kill fallback.
 *
 * The same sequence runs before `quitAndInstall` — an update never replaces
 * files under a running engine. Spawned engines are awaited via their child
 * exit event; adopted engines emit none, so they are awaited by PID polling
 * (per P7) — `waitForPidExit` below.
 */

export type QuitOutcome = 'clean' | 'forced'

export interface QuitOps {
  /** Abort the app's own /v1/events stream — an open SSE response would hold graceful shutdown open (per P1). */
  abortEvents(): void
  /** POST /v1/shutdown (202-then-exit). Failures fall through to wait/force-kill. */
  postShutdown(): Promise<void>
  /** Wait for engine exit (child exit event, or PID poll for adopted engines). */
  waitExit(timeoutMs: number): Promise<boolean>
  /** Last resort: kill the engine process (its Job Object / process-group backstop reaps children). */
  forceKill(): void | Promise<void>
}

export const QUIT_TIMEOUT_MS = 10_000

export async function runQuitSequence(
  ops: QuitOps,
  opts: { timeoutMs?: number } = {}
): Promise<QuitOutcome> {
  const timeoutMs = opts.timeoutMs ?? QUIT_TIMEOUT_MS
  ops.abortEvents()
  try {
    await ops.postShutdown()
  } catch {
    // Unreachable engine: still wait briefly (it may already be exiting), then force-kill.
  }
  if (await ops.waitExit(timeoutMs)) return 'clean'
  await ops.forceKill()
  return 'forced'
}

/**
 * Poll a PID until it exits or the timeout elapses (per P7).
 *
 * `isAlive`, `sleep`, and `monotonicMs` are injectable for tests; production
 * callers pass an `isAlive` backed by `process.kill(pid, 0)`.
 */
export async function waitForPidExit(
  pid: number,
  opts: {
    timeoutMs: number
    intervalMs?: number
    isAlive: (pid: number) => boolean
    sleep?: (ms: number) => Promise<void>
    monotonicMs?: () => number
  }
): Promise<boolean> {
  const intervalMs = opts.intervalMs ?? 200
  const sleep = opts.sleep ?? ((ms) => new Promise((resolve) => setTimeout(resolve, ms)))
  const monotonicMs = opts.monotonicMs ?? (() => performance.now())
  const deadline = monotonicMs() + opts.timeoutMs
  for (;;) {
    if (!opts.isAlive(pid)) return true
    if (monotonicMs() >= deadline) return false
    await sleep(intervalMs)
  }
}

/**
 * `process.kill(pid, 0)`-based liveness probe (EPERM counts as alive).
 *
 * pid <= 0 is never a single process — kill(0)/kill(-n) address process
 * groups — so it is reported dead without signaling anything.
 */
export function pidIsAlive(pid: number): boolean {
  if (pid <= 0) return false
  try {
    process.kill(pid, 0)
    return true
  } catch (err) {
    return (err as NodeJS.ErrnoException).code === 'EPERM'
  }
}
