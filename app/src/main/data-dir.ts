import { join } from 'node:path'

/**
 * Resolve the engine data directory exactly like the engine does.
 *
 * Mirrors `podcast_reader/engine/settings.py:data_dir()`: the
 * `PODCAST_READER_DATA_DIR` env var (with a leading `~` expanded), else
 * `<home>/PodcastReader`. The directory is created by the engine, not here —
 * the app only reads from it.
 *
 * Divergence, by design: Python's `Path.expanduser` also resolves the
 * `~user` form; the app rejects it with a clear startup error
 * (`DataDirError`) rather than mis-resolving it as a relative path and
 * silently reading a different directory than the engine writes.
 */
export class DataDirError extends Error {}

export function resolveDataDir(env: Record<string, string | undefined>, home: string): string {
  const override = env['PODCAST_READER_DATA_DIR']
  if (override) return expandUser(override, home)
  return join(home, 'PodcastReader')
}

/** Expand a leading `~` (bare-`~` form only; the `~user` form is rejected). */
function expandUser(p: string, home: string): string {
  if (p === '~') return home
  if (p.startsWith('~/')) return join(home, p.slice(2))
  if (p.startsWith('~')) {
    throw new DataDirError(
      `PODCAST_READER_DATA_DIR: the ~user form (${JSON.stringify(p)}) is not supported by the ` +
        `desktop app — use ~/ or an absolute path`
    )
  }
  return p
}
