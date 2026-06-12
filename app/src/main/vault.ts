import { mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs'
import { dirname } from 'node:path'

/**
 * safeStorage-backed key vault (design decision 5, spec: safeStorage key
 * vault). `vault.json` in the app's userData holds
 * `{provider: base64(safeStorage.encryptString(key))}` — ciphertext at rest
 * app-side; nothing key-shaped ever lands on the engine's disk. Keys reach
 * the engine only via `PUT /v1/keys` pushes (EngineManager).
 *
 * When OS-level encryption is unavailable (headless Linux dev), the vault
 * holds keys in main-process memory for the session and never writes the
 * file — plaintext is never persisted.
 */

/** The subset of Electron's `safeStorage` the vault uses (test seam). */
export interface SafeStorageLike {
  isEncryptionAvailable(): boolean
  encryptString(plainText: string): Buffer
  decryptString(encrypted: Buffer): string
}

export type KeyStorageMode = 'encrypted' | 'session-memory'

export class KeyVault {
  readonly mode: KeyStorageMode
  private entries = new Map<string, string>() // provider -> plaintext key (decrypted on load)

  constructor(
    private readonly vaultPath: string,
    private readonly safeStorage: SafeStorageLike,
    private readonly log: (message: string) => void = console.warn
  ) {
    this.mode = safeStorage.isEncryptionAvailable() ? 'encrypted' : 'session-memory'
    if (this.mode === 'encrypted') this.load()
  }

  /** Set a provider key; an empty string removes the entry (engine env-fallback restored by the push). */
  setKey(provider: string, key: string): void {
    if (key === '') {
      this.entries.delete(provider)
    } else {
      this.entries.set(provider, key)
    }
    if (this.mode === 'encrypted') this.persist()
  }

  /** Decrypted provider→key map, for push-at-engine-start. Never send to the renderer. */
  keys(): Record<string, string> {
    return Object.fromEntries(this.entries)
  }

  /** Providers with a stored key (safe for the renderer — names only). */
  providers(): string[] {
    return [...this.entries.keys()]
  }

  private load(): void {
    let raw: string
    try {
      raw = readFileSync(this.vaultPath, 'utf8')
    } catch (err) {
      // Only a missing file means a fresh vault. Anything else (EACCES,
      // EISDIR, …) is a present-but-unreadable vault: quarantine it so a
      // later persist() can never silently clobber the stored keys.
      if ((err as NodeJS.ErrnoException).code === 'ENOENT') return
      this.quarantine(`unreadable (${String(err)})`)
      return
    }
    let parsed: unknown
    try {
      parsed = JSON.parse(raw)
    } catch (err) {
      this.quarantine(`invalid JSON (${String(err)})`)
      return
    }
    if (typeof parsed !== 'object' || parsed === null) {
      this.quarantine('not a JSON object')
      return
    }
    for (const [provider, ciphertext] of Object.entries(parsed as Record<string, unknown>)) {
      if (typeof ciphertext !== 'string') continue
      try {
        this.entries.set(provider, this.safeStorage.decryptString(Buffer.from(ciphertext, 'base64')))
      } catch {
        // undecryptable (keychain changed): drop the entry rather than crash
      }
    }
  }

  /**
   * Move a corrupt/unreadable vault aside with a timestamped rename (the
   * engine's journal quarantine pattern, `engine/jobs.py:_recover_journal`)
   * and start empty — the user's ciphertext is preserved for inspection.
   */
  private quarantine(reason: string): void {
    const stamp = new Date().toISOString().replace(/[:.]/g, '-')
    const corruptPath = `${this.vaultPath}.corrupt-${stamp}`
    try {
      renameSync(this.vaultPath, corruptPath)
      this.log(`key vault ${reason}; quarantined to ${corruptPath}, starting empty`)
    } catch (renameErr) {
      this.log(
        `key vault ${reason}; quarantine rename failed (${String(renameErr)}), starting empty`
      )
    }
  }

  private persist(): void {
    const payload: Record<string, string> = {}
    for (const [provider, key] of this.entries) {
      payload[provider] = this.safeStorage.encryptString(key).toString('base64')
    }
    mkdirSync(dirname(this.vaultPath), { recursive: true })
    const tmp = `${this.vaultPath}.tmp`
    writeFileSync(tmp, JSON.stringify(payload, null, 2), { mode: 0o600 })
    renameSync(tmp, this.vaultPath)
  }
}
