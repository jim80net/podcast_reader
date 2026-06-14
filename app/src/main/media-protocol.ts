/**
 * The privileged `app://media/<source_id>` protocol handler (app-shell spec,
 * design F-section "Main — media-protocol.ts").
 *
 * This is the *internal* in-app resource scheme — distinct from the external
 * OS deep-link `podcast-reader://` (protocol.ts). It is registered privileged
 * (standard + secure + stream + supportFetchAPI) at module top level in
 * index.ts and bound via `protocol.handle('app', …)` at ready.
 *
 * Trusted nowhere: the `source_id` is validated against the library-key
 * pattern (exactly what `library.source_identity` produces — a sha256
 * hexdigest), the handler only ever targets the loopback engine (no arbitrary
 * URL, no SSRF), it adds the engine bearer token the renderer never holds,
 * forwards the inbound `Range` header, and returns the engine `Response`
 * verbatim so the 206 status + `Content-Range` propagate to the media element
 * and the body streams without buffering the whole file in the main process.
 */

/** The sha256-hexdigest shape `library.source_identity` emits (library.py:34). */
export const SOURCE_ID_PATTERN = /^[0-9a-f]{64}$/

/** Loopback engine coordinates for the handler; null until the engine is ready. */
export interface EngineAccess {
  baseUrl: string
  token: string
}

/**
 * Build the `app://` request handler.
 *
 * @param access  Resolves the loopback engine base URL + token, or null when
 *                the engine is not yet ready.
 * @param fetchFn Injectable fetch (test seam); defaults to the global fetch.
 */
export function createMediaProtocolHandler(
  access: () => EngineAccess | null,
  fetchFn: typeof fetch = fetch
): (request: Request) => Promise<Response> {
  return async (request: Request): Promise<Response> => {
    const url = new URL(request.url)
    // app://media/<source_id> — the host is the resource class.
    if (url.hostname !== 'media') return new Response('not found', { status: 404 })
    const sourceId = url.pathname.replace(/^\/+/, '')
    if (!SOURCE_ID_PATTERN.test(sourceId)) {
      // Reject without ever contacting the engine — no traversal, no SSRF.
      return new Response('bad media id', { status: 400 })
    }
    const engine = access()
    if (engine === null) return new Response('engine not ready', { status: 503 })

    const headers: Record<string, string> = { authorization: `Bearer ${engine.token}` }
    const range = request.headers.get('range')
    if (range !== null) headers['range'] = range

    // Verbatim pass-through: returning the engine Response streams its body and
    // propagates its status/headers (206 + Content-Range) to the media element.
    // Forward the abort signal so a rapid seek/teardown cancels the upstream
    // fetch instead of leaving it running (cubic P2).
    return fetchFn(`${engine.baseUrl}/v1/media/${sourceId}`, {
      method: 'GET',
      headers,
      signal: request.signal
    })
  }
}
