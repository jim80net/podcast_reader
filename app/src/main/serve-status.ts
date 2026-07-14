/** Strict, fail-closed parser for `tailscale serve status --json`. */

export type ServeStatus =
  | { kind: 'empty' }
  | { kind: 'mapping'; target: string; url: string }
  | { kind: 'conflict'; reason: string }

type JsonObject = Record<string, unknown>

const ROOT_KEYS = new Set(['TCP', 'Web', 'AllowFunnel', 'Foreground', 'Services'])
const CONFIG_KEYS = new Set(['TCP', 'Web', 'AllowFunnel'])

function object(value: unknown): JsonObject | null {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
    ? (value as JsonObject)
    : null
}

function ownKeysOnly(value: JsonObject, allowed: ReadonlySet<string>): boolean {
  return Object.keys(value).every((key) => allowed.has(key))
}

function validTailnetHostname(host: string): boolean {
  if (host.length > 253 || !host.endsWith('.ts.net')) return false
  return host.split('.').every(
    (label) =>
      label.length >= 1 &&
      label.length <= 63 &&
      /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/.test(label)
  )
}

function conflict(reason: string): ServeStatus {
  return { kind: 'conflict', reason }
}

/**
 * Classify listener 443 without guessing through novel output. The parser
 * validates the complete known node-level schema first: a future or malformed
 * shape is conflict-equivalent even when the fragment for 443 looks familiar.
 */
export function classifyServeStatus(text: string): ServeStatus {
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    return conflict('Tailscale Serve returned malformed JSON')
  }
  const root = object(parsed)
  if (root === null || !ownKeysOnly(root, ROOT_KEYS)) {
    return conflict('Tailscale Serve returned an unexpected status shape')
  }

  const services = root['Services'] === undefined ? {} : object(root['Services'])
  const foreground = root['Foreground'] === undefined ? {} : object(root['Foreground'])
  if (services === null || foreground === null) {
    return conflict('Tailscale Serve returned an unexpected status shape')
  }
  // Services use distinct virtual IPs, but M1 does not own or mutate them.
  // Conservatively stop on their presence until that schema gets a reviewed
  // parser of its own instead of guessing across a new administration plane.
  if (Object.keys(services).length > 0) {
    return conflict('Tailscale Services are configured; private access will not modify them')
  }

  const configs: JsonObject[] = []
  const background = Object.fromEntries(
    Object.entries(root).filter(([key]) => CONFIG_KEYS.has(key))
  )
  configs.push(background)
  for (const rawConfig of Object.values(foreground)) {
    const config = object(rawConfig)
    if (config === null || !ownKeysOnly(config, CONFIG_KEYS)) {
      return conflict('Tailscale Serve returned an unexpected foreground status shape')
    }
    configs.push(config)
  }

  const classified = configs.map(classifyConfig)
  const firstConflict = classified.find((status) => status.kind === 'conflict')
  if (firstConflict?.kind === 'conflict') return firstConflict
  const mappings = classified.filter((status): status is Extract<ServeStatus, { kind: 'mapping' }> =>
    status.kind === 'mapping'
  )
  if (mappings.length === 0) return { kind: 'empty' }
  if (mappings.length > 1) return conflict('HTTPS 443 has multiple background or foreground owners')
  return mappings[0] ?? conflict('HTTPS 443 status is internally inconsistent')
}

function classifyConfig(root: JsonObject): ServeStatus {

  const tcp = root['TCP'] === undefined ? {} : object(root['TCP'])
  const web = root['Web'] === undefined ? {} : object(root['Web'])
  const funnel = root['AllowFunnel'] === undefined ? {} : object(root['AllowFunnel'])
  if (tcp === null || web === null || funnel === null) {
    return conflict('Tailscale Serve returned an unexpected status shape')
  }
  if (Object.keys(funnel).length > 0) {
    return conflict('Tailscale Funnel is configured; private access will not modify it')
  }

  for (const [portText, listener] of Object.entries(tcp)) {
    if (!/^[1-9]\d{0,4}$/.test(portText) || Number(portText) > 65535) {
      return conflict('Tailscale Serve returned an unexpected TCP listener name')
    }
    const config = object(listener)
    if (
      config === null ||
      !ownKeysOnly(config, new Set(['HTTP', 'HTTPS', 'TCP', 'TLS'])) ||
      Object.keys(config).length === 0 ||
      !Object.values(config).every((flag) => flag === true)
    ) {
      return conflict('Tailscale Serve returned an unexpected TCP listener shape')
    }
  }

  const candidates: Array<{ host: string; target: string; rootOnly: boolean }> = []
  for (const [hostPort, rawWebConfig] of Object.entries(web)) {
    const separator = hostPort.lastIndexOf(':')
    const portText = hostPort.slice(separator + 1)
    if (
      separator <= 0 ||
      !validTailnetHostname(hostPort.slice(0, separator)) ||
      !/^[1-9]\d{0,4}$/.test(portText) ||
      Number(portText) > 65535
    ) {
      return conflict('Tailscale Serve returned an unexpected web listener name')
    }
    const webConfig = object(rawWebConfig)
    if (webConfig === null || !ownKeysOnly(webConfig, new Set(['Handlers']))) {
      return conflict('Tailscale Serve returned an unexpected web listener shape')
    }
    const handlers = object(webConfig['Handlers'])
    if (handlers === null || Object.keys(handlers).length === 0) {
      return conflict('Tailscale Serve returned an unexpected handler shape')
    }
    for (const rawHandler of Object.values(handlers)) {
      const handler = object(rawHandler)
      if (
        handler === null ||
        !ownKeysOnly(handler, new Set(['Proxy'])) ||
        typeof handler['Proxy'] !== 'string'
      ) {
        return conflict('Tailscale Serve returned an unexpected handler shape')
      }
    }
    if (portText === '443') {
      const paths = Object.keys(handlers)
      const rootHandler = object(handlers['/'])
      candidates.push({
        host: hostPort.slice(0, separator),
        target: rootHandler !== null && typeof rootHandler['Proxy'] === 'string'
          ? rootHandler['Proxy']
          : '',
        rootOnly: paths.length === 1 && paths[0] === '/'
      })
    }
  }

  const tcp443 = object(tcp['443'])
  const hasHttps443 =
    tcp443 !== null && Object.keys(tcp443).length === 1 && tcp443['HTTPS'] === true
  if (!hasHttps443 && candidates.length === 0) return { kind: 'empty' }
  if (!hasHttps443 || candidates.length === 0) {
    return conflict('HTTPS 443 status is internally inconsistent')
  }
  if (candidates.length !== 1 || candidates[0]?.rootOnly !== true) {
    return conflict('HTTPS 443 has a non-root or ambiguous web handler')
  }
  const candidate = candidates[0]
  if (candidate === undefined || candidate.target === '') {
    return conflict('HTTPS 443 has a non-root or ambiguous web handler')
  }
  return {
    kind: 'mapping',
    target: candidate.target,
    url: `https://${candidate.host}`
  }
}
