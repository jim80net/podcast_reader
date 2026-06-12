/**
 * `podcast-reader://` protocol URL validation (design decision 7).
 *
 * Registered everywhere, trusted nowhere: accept only scheme
 * `podcast-reader`, host `transcribe`, and an `http(s)` `url` parameter.
 * Anything else is rejected (callers log the rejection). Valid URLs are only
 * ever submitted with `requires_confirmation: true` — nothing
 * protocol-initiated auto-executes.
 */

export interface TranscribeRequest {
  url: string
}

export function parseProtocolUrl(raw: string): TranscribeRequest | null {
  let parsed: URL
  try {
    parsed = new URL(raw)
  } catch {
    return null
  }
  if (parsed.protocol !== 'podcast-reader:') return null
  // Exactly podcast-reader://transcribe?...: the host comparison already
  // excludes spoofed hosts and explicit ports; additionally reject embedded
  // credentials and any path beyond a bare trailing slash.
  if (parsed.host !== 'transcribe') return null
  if (parsed.username !== '' || parsed.password !== '') return null
  if (parsed.pathname !== '' && parsed.pathname !== '/') return null
  const target = parsed.searchParams.get('url')
  if (target === null || target === '') return null
  let targetUrl: URL
  try {
    targetUrl = new URL(target)
  } catch {
    return null
  }
  if (targetUrl.protocol !== 'http:' && targetUrl.protocol !== 'https:') return null
  return { url: target }
}

/**
 * Select the protocol URL from a Windows `second-instance` commandLine.
 *
 * Per P8: scan for the entry matching `^podcast-reader://` (case-insensitive
 * scheme) — never pop the last argv entry blindly, since Chromium switches
 * and stray arguments may follow or precede it.
 */
export function selectProtocolArgv(argv: readonly string[]): string | null {
  return argv.find((arg) => /^podcast-reader:\/\//i.test(arg)) ?? null
}
