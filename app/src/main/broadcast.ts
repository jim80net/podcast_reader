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
    window.webContents.send(channel, payload)
  }
}
