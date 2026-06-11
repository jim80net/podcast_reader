import { join } from 'node:path'

/**
 * Resolve the engine data directory exactly like the engine does.
 *
 * Mirrors `podcast_reader/engine/settings.py:data_dir()`: the
 * `PODCAST_READER_DATA_DIR` env var (with a leading `~` expanded, as
 * `Path.expanduser` does), else `<home>/PodcastReader`. The directory is
 * created by the engine, not here — the app only reads from it.
 */
export function resolveDataDir(env: Record<string, string | undefined>, home: string): string {
  const override = env['PODCAST_READER_DATA_DIR']
  if (override) return expandUser(override, home)
  return join(home, 'PodcastReader')
}

/** Expand a leading `~` (only the bare-`~` form; `~user` is not supported). */
function expandUser(p: string, home: string): string {
  if (p === '~') return home
  if (p.startsWith('~/')) return join(home, p.slice(2))
  return p
}
