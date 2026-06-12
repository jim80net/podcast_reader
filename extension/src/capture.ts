import { registrableDomainOfUrl } from './etld'

/**
 * Cookie-capture targeting (ext-cookie-capture spec): from a failed job's
 * source URL, derive the registrable-domain guess (per U4) and the optional
 * permission request scoped to it — `cookies` plus https origins for the
 * domain and its subdomains only, requested at click time. `http` origins
 * are deliberately never requested (per U6: real login cookies carry
 * `Secure`, so an http-only jar is pointless).
 *
 * The domain the jar is ultimately DECLARED under is derived from the
 * captured cookies themselves (`declaredDomain`, per V1): the eTLD+1
 * heuristic can over-deepen on generic-label collisions (see etld.ts), and
 * a too-deep declaration makes the engine reject the parent-domain login
 * cookie — the very cookie the capture exists for.
 */

export interface CaptureTarget {
  /** The heuristic registrable-domain guess (permission scope + fallback). */
  domain: string
  /** The source URL's host — what `declaredDomain` suffix-matches against. */
  sourceHost: string
  /** Origin patterns for `chrome.permissions.request`. */
  origins: [string, string]
  /** The URL-keyed `chrome.cookies.getAll({url})` query (https-normalized). */
  queryUrl: string
}

export function captureTarget(sourceUrl: string): CaptureTarget | null {
  const domain = registrableDomainOfUrl(sourceUrl)
  if (domain === null) return null
  let parsed: URL
  try {
    parsed = new URL(sourceUrl)
  } catch {
    return null
  }
  // https-normalize the cookie query: only https origins are granted (per
  // U6), and Secure cookies — the ones logins ride on — are only visible
  // through an https URL key anyway.
  parsed.protocol = 'https:'
  return {
    domain,
    sourceHost: parsed.hostname.toLowerCase().replace(/\.$/, ''),
    origins: [`https://${domain}/*`, `https://*.${domain}/*`],
    queryUrl: parsed.toString()
  }
}

/**
 * The domain a captured jar is declared under (per V1): the broadest
 * dot-stripped cookie domain that (a) suffix-matches the source host and
 * (b) has at least two labels — i.e. evidence from the browser's own cookie
 * store — falling back to the heuristic guess when no captured cookie is
 * broader.
 *
 * Two constraints keep every captured cookie valid against the engine's
 * suffix check (engine/cookies.py):
 *
 * - Candidates must suffix-match the source host: the domain-keyed
 *   `getAll({domain})` query can return cookies on deeper sibling
 *   subdomains, and declaring one of those would invalidate the rest of
 *   the jar.
 * - The declaration never narrows below the heuristic: those same
 *   domain-keyed cookies only suffix-match domains at or above it.
 */
export function declaredDomain(
  target: CaptureTarget,
  cookies: readonly { domain: string }[]
): string {
  let best = target.domain
  let bestDepth = best.split('.').length
  for (const cookie of cookies) {
    const domain = cookie.domain.replace(/^\.+/, '').toLowerCase()
    const labels = domain.split('.')
    if (labels.length < 2 || labels.some((label) => label === '')) continue
    if (domain !== target.sourceHost && !target.sourceHost.endsWith(`.${domain}`)) continue
    if (labels.length < bestDepth) {
      best = domain
      bestDepth = labels.length
    }
  }
  return best
}
