import { join } from 'node:path'

/** How the engine command was chosen (design decision 2's three-way chain). */
export type EnginePosture = 'packaged' | 'override' | 'dev'

export interface ResolvedEngineCommand {
  argv: string[]
  posture: EnginePosture
}

/**
 * Split `PODCAST_READER_ENGINE_CMD` into an argv.
 *
 * Documented contract (per P6): a plain whitespace split — no quoting, no
 * escaping — so paths containing spaces are unsupported in the override; use
 * the packaged or `uv` postures for those. Returns null when the value is
 * unset or blank (the chain then falls through to the dev fallback).
 */
export function splitEngineCmd(value: string | undefined): string[] | null {
  if (value === undefined) return null
  const parts = value.trim().split(/\s+/).filter(Boolean)
  return parts.length > 0 ? parts : null
}

/**
 * Resolve the engine spawn command (design decision 2):
 * 1. packaged engine `<resourcesPath>/engine/podcast-reader-engine serve`
 * 2. `PODCAST_READER_ENGINE_CMD` (whitespace-split, used verbatim as argv)
 * 3. dev fallback `uv run podcast-reader serve`
 */
export function resolveEngineCommand(opts: {
  resourcesPath: string | null
  env: Record<string, string | undefined>
  fileExists: (path: string) => boolean
  platform: NodeJS.Platform
}): ResolvedEngineCommand {
  if (opts.resourcesPath !== null) {
    const name = opts.platform === 'win32' ? 'podcast-reader-engine.exe' : 'podcast-reader-engine'
    const exe = join(opts.resourcesPath, 'engine', name)
    if (opts.fileExists(exe)) return { argv: [exe, 'serve'], posture: 'packaged' }
  }
  const override = splitEngineCmd(opts.env['PODCAST_READER_ENGINE_CMD'])
  if (override !== null) return { argv: override, posture: 'override' }
  return { argv: ['uv', 'run', 'podcast-reader', 'serve'], posture: 'dev' }
}
