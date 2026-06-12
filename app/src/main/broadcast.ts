/**
 * Renderer broadcast with destroyed-window guards. `webContents.send` on a
 * destroyed target throws ("Object has been destroyed"), and a broadcast can
 * race window teardown (quit path, SSE forwarding loop) — so every send is
 * gated on both the window and its webContents being alive.
 *
 * Structural `BroadcastWindowLike` keeps this testable without Electron.
 */

/** The subset of `BrowserWindow` the broadcast relies on (test seam). */
export interface BroadcastWindowLike {
  isDestroyed(): boolean
  webContents: {
    isDestroyed(): boolean
    send(channel: string, payload: unknown): void
  }
}

/** Send `payload` on `channel` to every window that is still alive. */
export function broadcastTo(
  windows: readonly BroadcastWindowLike[],
  channel: string,
  payload: unknown
): void {
  for (const window of windows) {
    if (window.isDestroyed() || window.webContents.isDestroyed()) continue
    try {
      window.webContents.send(channel, payload)
    } catch {
      // Native teardown can race the liveness check (TOCTOU): a window
      // destroyed between the guard and the send must never propagate a
      // throw into the SSE loop or the quit path. Skipping is correct —
      // the window is gone, there is nothing to notify.
    }
  }
}
