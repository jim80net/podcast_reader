/**
 * Registrable-domain (eTLD+1) derivation for cookie capture (per U4 —
 * host-based derivation breaks on subdomains: parent-domain cookies are not
 * returned by `getAll({domain})` and would then fail the engine's
 * suffix-match validation).
 *
 * Documented heuristic, not the full Public Suffix List (no heavy
 * dependency):
 *
 *   eTLD+1 = the last two labels, EXCEPT when the TLD is a two-letter
 *   country code and the second-level label is a generic category label
 *   (co/com/net/org/gov/edu/ac/…), in which case it is the last three
 *   labels (bbc.co.uk, abc.net.au, nico.co.jp, …).
 *
 * Known limitations (per V1 — both directions of error are reachable, so
 * the heuristic is only a permission-scoping GUESS; the jar's declared
 * domain is derived from the captured cookies in capture.ts
 * `declaredDomain`):
 *
 * - Too DEEP: real registrable domains whose second level collides with
 *   the generic list over-deepen (mail.web.de → mail.web.de instead of
 *   web.de, api.id.me → api.id.me instead of id.me). Declared as-is, the
 *   parent-domain login cookie would fail the engine's suffix validation
 *   and the whole PUT would 400.
 * - Too SHALLOW: multi-tenant private suffixes (github.io, …) resolve to
 *   the suffix itself — the permission prompt is then broader than ideal
 *   but the capture still works (the engine only suffix-validates).
 *
 * IP literals and single-label hosts (localhost) return null: there is no
 * registrable domain to scope a jar to.
 */

/** Generic second-level category labels seen under two-letter ccTLDs. */
const CC_SECOND_LEVEL = new Set([
  'co', 'com', 'net', 'org', 'gov', 'gob', 'govt', 'edu', 'ac', 'sch',
  'go', 'or', 'ne', 'ad', 'ed', 'lg', 're', 'mil', 'asn', 'id', 'web',
  'firm', 'gen', 'ind', 'res', 'plc', 'ltd'
])

export function registrableDomain(host: string): string | null {
  const lowered = host.toLowerCase().replace(/\.$/, '')
  if (lowered === '' || lowered.includes(':')) return null // IPv6 / port leak
  if (/^[0-9.]+$/.test(lowered)) return null // IPv4 literal
  const labels = lowered.split('.')
  if (labels.length < 2 || labels.some((label) => label === '')) return null
  const tld = labels[labels.length - 1] ?? ''
  const second = labels[labels.length - 2] ?? ''
  if (labels.length >= 3 && tld.length === 2 && CC_SECOND_LEVEL.has(second)) {
    return labels.slice(-3).join('.')
  }
  return labels.slice(-2).join('.')
}

/** The registrable domain of an http(s) URL's host; null for anything else. */
export function registrableDomainOfUrl(url: string): string | null {
  let parsed: URL
  try {
    parsed = new URL(url)
  } catch {
    return null
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return null
  return registrableDomain(parsed.hostname)
}
