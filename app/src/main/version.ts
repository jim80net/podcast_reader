/**
 * Engine version floor for adoption (per P3/Q1).
 *
 * The minimum engine version carrying the endpoints this app depends on —
 * since 0.3.0 the pack surface (`GET /v1/packs`,
 * `POST /v1/packs/{id}/install`, `DELETE /v1/packs/{id}`) and pack events
 * on `GET /v1/events`. The engine package version (pyproject.toml) bumps to
 * 0.3.0 alongside those routes (the established bump-together pattern), so
 * a 0.2.0 engine (lacking them) falls below the floor and is stale-killed;
 * bump this constant together with any future engine version bump that
 * this app depends on.
 */
export const MIN_ENGINE_VERSION = '0.3.0'

/**
 * `version >= min`, by dotted numeric core with missing parts as 0.
 *
 * Any suffix after the numeric core (e.g. `-dev`, `-rc1` — `engine_version()`
 * reports `0.0.0-dev` when the package is not installed) marks a pre-release:
 * lower than the plain release of the same core. Unparseable versions never
 * satisfy the floor.
 */
export function versionAtLeast(version: string, min: string): boolean {
  const v = parseVersion(version)
  if (v === null) return false
  const m = parseVersion(min)
  if (m === null) return false
  const length = Math.max(v.core.length, m.core.length)
  for (let i = 0; i < length; i++) {
    const a = v.core[i] ?? 0
    const b = m.core[i] ?? 0
    if (a !== b) return a > b
  }
  // Equal cores: a pre-release is below the plain release.
  if (v.prerelease && !m.prerelease) return false
  return true
}

function parseVersion(version: string): { core: number[]; prerelease: boolean } | null {
  const match = /^(\d+(?:\.\d+)*)(.*)$/.exec(version.trim())
  if (match === null || match[1] === undefined) return null
  return {
    core: match[1].split('.').map(Number),
    prerelease: (match[2] ?? '') !== ''
  }
}
