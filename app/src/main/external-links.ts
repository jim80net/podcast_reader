/**
 * External-navigation + YouTube-embed policy for the credential-free renderer.
 *
 * The renderer is a single `file://` document (loadFile in production). Two
 * consequences of that origin are handled here:
 *
 *  1. Any http(s) URL the renderer tries to open — a `target="_blank"` link
 *     such as the "Watch on YouTube" fallback or a provider key-docs link, or
 *     an attempted top-level navigation — must open in the user's OS default
 *     browser (where they're logged in), never a chromeless in-app Electron
 *     window. `isExternalWebUrl` is the predicate; index.ts routes matches to
 *     `shell.openExternal` and denies the in-app window.
 *
 *  2. YouTube's embedded player rejects requests that carry no valid HTTP
 *     `Referer` with "Error 153: Video player configuration error" (enforced
 *     since late 2025). A `file://` origin sends no usable Referer, so we
 *     inject one on YouTube-bound requests ONLY (scoped by host filter), so the
 *     engine (127.0.0.1) and `app://media` traffic never receive a spoofed
 *     Referer.
 */

/** True for http/https URLs — the ones that belong in the OS default browser. */
export function isExternalWebUrl(url: string): boolean {
  let parsed: URL
  try {
    parsed = new URL(url)
  } catch {
    return false
  }
  return parsed.protocol === 'http:' || parsed.protocol === 'https:'
}

/** A valid https Referer to stand in for the file:// embedding page. */
export const YOUTUBE_REFERER = 'https://www.youtube.com/'

/**
 * webRequest URL filter for YouTube embed traffic: the nocookie embed document,
 * its static assets (ytimg), media segments (googlevideo), and the main domain
 * the player may redirect through. Scoped so no other host gets the Referer.
 */
export const YOUTUBE_URL_FILTER: { urls: string[] } = {
  urls: [
    'https://*.youtube.com/*',
    'https://*.youtube-nocookie.com/*',
    'https://*.ytimg.com/*',
    'https://*.googlevideo.com/*'
  ]
}
