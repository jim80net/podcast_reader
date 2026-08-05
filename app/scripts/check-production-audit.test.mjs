import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { evaluateAudit } from './check-production-audit.mjs'

const clean = { vulnerabilities: {} }
const high = {
  vulnerabilities: {
    'js-yaml': {
      severity: 'high',
      via: [{ source: 1123911, severity: 'high', url: 'https://github.com/advisories/GHSA-52cp-r559-cp3m' }]
    }
  }
}
const emptyRegistry = { schema_version: 1, exceptions: [] }
const exception = {
  package: 'js-yaml',
  advisory: 'GHSA-52cp-r559-cp3m',
  issue: 'https://github.com/jim80net/podcast_reader/issues/185',
  reason: 'Temporary test exception.',
  expires: '2026-09-01'
}

describe('production audit gate', () => {
  it('accepts a clean production report with an empty registry', () => {
    assert.deepEqual(evaluateAudit(clean, emptyRegistry, '2026-08-05'), {
      errors: [], findings: [], excepted: []
    })
  })

  it('blocks an unexcepted high advisory', () => {
    const result = evaluateAudit(high, emptyRegistry, '2026-08-05')
    assert.equal(result.errors.length, 0)
    assert.deepEqual(result.findings.map((finding) => finding.key), [
      'js-yaml:GHSA-52cp-r559-cp3m'
    ])
  })

  it('accepts an exact, reviewed, unexpired exception', () => {
    const result = evaluateAudit(high, { schema_version: 1, exceptions: [exception] }, '2026-08-05')
    assert.equal(result.errors.length, 0)
    assert.equal(result.findings.length, 0)
    assert.deepEqual(result.excepted.map((finding) => finding.key), [
      'js-yaml:GHSA-52cp-r559-cp3m'
    ])
  })

  it('fails closed for expired, stale, duplicate, or malformed exceptions', () => {
    const expired = { ...exception, expires: '2026-08-05' }
    const malformed = { ...exception, advisory: '' }
    assert.match(
      evaluateAudit(high, { schema_version: 1, exceptions: [expired] }, '2026-08-05').errors.join('\n'),
      /expired/
    )
    assert.match(
      evaluateAudit(clean, { schema_version: 1, exceptions: [exception] }, '2026-08-05').errors.join('\n'),
      /stale/
    )
    assert.match(
      evaluateAudit(high, { schema_version: 1, exceptions: [exception, exception] }, '2026-08-05').errors.join('\n'),
      /duplicate/
    )
    assert.match(
      evaluateAudit(high, { schema_version: 1, exceptions: [malformed] }, '2026-08-05').errors.join('\n'),
      /must name a package and advisory/
    )
  })
})
