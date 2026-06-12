import type { PodcastReaderApi } from '../../shared/ipc'

declare global {
  interface Window {
    /** The preload contextBridge surface — the renderer's only engine access. */
    api: PodcastReaderApi
  }
}

export {}
