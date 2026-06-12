import { registrableDomainOfUrl } from './etld'

/**
 * Cookie-capture targeting (ext-cookie-capture spec): from a failed job's
 * source URL, derive the registrable domain (per U4) and the optional
 * permission request scoped to it — `cookies` plus https origins for the
 * domain and its subdomains only, requested at click time. `http` origins
 * are deliberately never requested (per U6: real login cookies carry
 * `Secure`, so an http-only jar is pointless).
 */

export interface CaptureTarget {
  /** The registrable domain the jar is declared under. */
  domain: string
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
    origins: [`https://${domain}/*`, `https://*.${domain}/*`],
    queryUrl: parsed.toString()
  }
}
