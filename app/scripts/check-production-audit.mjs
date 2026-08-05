import { spawnSync } from 'node:child_process'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const APP_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const EXCEPTIONS_PATH = resolve(APP_ROOT, 'security/npm-audit-exceptions.json')
const BLOCKING_SEVERITIES = new Set(['high', 'critical'])
const EXCEPTION_KEYS = ['advisory', 'expires', 'issue', 'package', 'reason']
const ISSUE_PATTERN = /^https:\/\/github\.com\/jim80net\/podcast_reader\/issues\/\d+$/

function exactKeys(value, expected) {
  return JSON.stringify(Object.keys(value).sort()) === JSON.stringify([...expected].sort())
}

function advisoryIds(vulnerability) {
  const ids = vulnerability.via
    .filter((item) => typeof item === 'object' && item !== null)
    .filter((item) => BLOCKING_SEVERITIES.has(item.severity))
    .map((item) => {
      const urlId = typeof item.url === 'string' ? item.url.split('/').at(-1) : undefined
      return urlId || String(item.source)
    })
  if (ids.length > 0) return [...new Set(ids)].sort()

  const inherited = vulnerability.via.filter((item) => typeof item === 'string').sort()
  return inherited.length > 0 ? inherited.map((name) => `via:${name}`) : ['package-level']
}

export function evaluateAudit(report, registry, today = new Date().toISOString().slice(0, 10)) {
  const errors = []
  if (report === null || typeof report !== 'object' || report.error !== undefined) {
    return { errors: ['npm audit did not return a usable report'], findings: [], excepted: [] }
  }
  if (!exactKeys(registry, ['schema_version', 'exceptions']) || registry.schema_version !== 1) {
    return { errors: ['exception registry must contain only schema_version: 1 and exceptions'], findings: [], excepted: [] }
  }
  if (!Array.isArray(registry.exceptions)) {
    return { errors: ['exception registry exceptions must be an array'], findings: [], excepted: [] }
  }

  const exceptions = []
  const seen = new Set()
  for (const [index, entry] of registry.exceptions.entries()) {
    if (entry === null || typeof entry !== 'object' || !exactKeys(entry, EXCEPTION_KEYS)) {
      errors.push(`exception ${index + 1} must contain exactly ${EXCEPTION_KEYS.join(', ')}`)
      continue
    }
    const key = `${entry.package}:${entry.advisory}`
    if (seen.has(key)) errors.push(`duplicate exception ${key}`)
    seen.add(key)
    const parsedExpiry = Date.parse(`${entry.expires}T00:00:00Z`)
    if (
      !/^\d{4}-\d{2}-\d{2}$/.test(entry.expires) ||
      Number.isNaN(parsedExpiry) ||
      new Date(parsedExpiry).toISOString().slice(0, 10) !== entry.expires
    ) {
      errors.push(`exception ${key} has an invalid expires date`)
    } else if (entry.expires <= today) {
      errors.push(`exception ${key} expired on ${entry.expires}`)
    }
    if (!ISSUE_PATTERN.test(entry.issue)) errors.push(`exception ${key} must link a public tracking issue`)
    if (typeof entry.reason !== 'string' || entry.reason.trim() === '') {
      errors.push(`exception ${key} must include a reason`)
    }
    if (typeof entry.package !== 'string' || entry.package === '' || typeof entry.advisory !== 'string' || entry.advisory === '') {
      errors.push(`exception ${index + 1} must name a package and advisory`)
    }
    exceptions.push({ ...entry, key })
  }

  const vulnerabilities = report.vulnerabilities
  if (vulnerabilities === null || typeof vulnerabilities !== 'object') {
    return { errors: [...errors, 'npm audit report lacks vulnerabilities'], findings: [], excepted: [] }
  }
  const findings = Object.entries(vulnerabilities)
    .filter(([, vulnerability]) => BLOCKING_SEVERITIES.has(vulnerability.severity))
    .flatMap(([packageName, vulnerability]) =>
      advisoryIds(vulnerability).map((advisory) => ({
        package: packageName,
        advisory,
        severity: vulnerability.severity,
        key: `${packageName}:${advisory}`
      }))
    )
  const exceptionKeys = new Set(exceptions.map((entry) => entry.key))
  const findingKeys = new Set(findings.map((finding) => finding.key))
  const blocked = findings.filter((finding) => !exceptionKeys.has(finding.key))
  const excepted = findings.filter((finding) => exceptionKeys.has(finding.key))
  for (const entry of exceptions) {
    if (!findingKeys.has(entry.key)) errors.push(`stale exception ${entry.key} matches no current finding`)
  }
  return { errors, findings: blocked, excepted }
}

function main() {
  const npmCli = process.env.npm_execpath
  const command = npmCli === undefined
    ? (process.platform === 'win32' ? 'npm.cmd' : 'npm')
    : process.execPath
  const args = npmCli === undefined
    ? ['audit', '--omit=dev', '--audit-level=high', '--json']
    : [npmCli, 'audit', '--omit=dev', '--audit-level=high', '--json']
  const audit = spawnSync(command, args, { cwd: APP_ROOT, encoding: 'utf8' })
  let report
  try {
    report = JSON.parse(audit.stdout)
  } catch {
    console.error('Production audit failed: npm did not emit valid JSON.')
    if (audit.stderr.trim() !== '') console.error(audit.stderr.trim())
    process.exitCode = 1
    return
  }

  const registry = JSON.parse(readFileSync(EXCEPTIONS_PATH, 'utf8'))
  const result = evaluateAudit(report, registry)
  for (const error of result.errors) console.error(`Production audit configuration error: ${error}`)
  for (const finding of result.findings) {
    console.error(`Production audit blocked: ${finding.severity} ${finding.package} (${finding.advisory})`)
  }
  for (const finding of result.excepted) {
    console.warn(`Production audit exception active: ${finding.severity} ${finding.package} (${finding.advisory})`)
  }
  console.log('Scope: runtime dependencies only (--omit=dev); development tooling is reviewed separately.')
  if (result.errors.length > 0 || result.findings.length > 0) process.exitCode = 1
  else console.log(`Production audit clean: ${result.excepted.length} reviewed exception(s), no unexcepted high/critical findings.`)
}

if (process.argv[1] !== undefined && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main()
