import { mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs'
import { dirname } from 'node:path'

/**
 * App-side configuration (design decision 10 of the download-manager change):
 * a small JSON file under Electron's userData dir. Today it holds exactly one
 * flag — `first_run_complete`, set when the setup wizard is completed or
 * skipped — gating whether the wizard auto-opens on launch. Engine-owned
 * state (settings, packs, jobs) never lives here.
 */

interface AppConfig {
  first_run_complete?: boolean
}

export class AppConfigStore {
  constructor(
    private readonly path: string,
    private readonly log: (message: string) => void = () => {}
  ) {}

  /** True once setup was completed or skipped; any unreadable config reads false. */
  isFirstRunComplete(): boolean {
    return this.read().first_run_complete === true
  }

  markFirstRunComplete(): void {
    this.write({ ...this.read(), first_run_complete: true })
  }

  private read(): AppConfig {
    let text: string
    try {
      text = readFileSync(this.path, 'utf8')
    } catch {
      return {} // absent: first launch
    }
    try {
      const parsed: unknown = JSON.parse(text)
      // Arrays are objects to typeof; spreading one in write() would persist
      // its indices as keys, so only a plain object passes the guard.
      if (typeof parsed === 'object' && parsed !== null && !Array.isArray(parsed)) {
        return parsed as AppConfig
      }
    } catch (err) {
      this.log(`app config at ${this.path} is unreadable, treating as empty: ${String(err)}`)
    }
    return {}
  }

  private write(config: AppConfig): void {
    // Atomic-by-rename, the same discipline as the engine's state files: a
    // crash mid-write must never leave a half-written config behind.
    mkdirSync(dirname(this.path), { recursive: true })
    const tmp = `${this.path}.tmp`
    writeFileSync(tmp, JSON.stringify(config))
    renameSync(tmp, this.path)
  }
}
