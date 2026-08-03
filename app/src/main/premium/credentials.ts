import { mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs'
import { dirname } from 'node:path'
import type { SafeStorageLike } from '../vault'

export interface PremiumCredentials { subject: string; refreshToken: string }

export class PremiumCredentialStore {
  readonly mode: 'encrypted' | 'session-memory'
  private value: PremiumCredentials | null = null

  constructor(private readonly path: string, private readonly safeStorage: SafeStorageLike) {
    this.mode = safeStorage.isEncryptionAvailable() ? 'encrypted' : 'session-memory'
    if (this.mode === 'encrypted') this.load()
  }

  get(): PremiumCredentials | null { return this.value === null ? null : { ...this.value } }

  set(value: PremiumCredentials | null): void {
    this.value = value === null ? null : { ...value }
    if (this.mode === 'encrypted') this.persist()
  }

  private load(): void {
    try {
      const envelope = JSON.parse(readFileSync(this.path, 'utf8')) as unknown
      if (typeof envelope !== 'object' || envelope === null) throw new Error('invalid envelope')
      const ciphertext = (envelope as { ciphertext?: unknown }).ciphertext
      if (ciphertext === null) return
      if (typeof ciphertext !== 'string') throw new Error('invalid envelope')
      const decoded = JSON.parse(this.safeStorage.decryptString(Buffer.from(ciphertext, 'base64'))) as unknown
      if (typeof decoded !== 'object' || decoded === null) throw new Error('invalid credentials')
      const { subject, refreshToken } = decoded as Record<string, unknown>
      if (typeof subject !== 'string' || subject === '' || typeof refreshToken !== 'string' || refreshToken.length < 20) throw new Error('invalid credentials')
      this.value = { subject, refreshToken }
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') return
      try { renameSync(this.path, `${this.path}.corrupt-${Date.now()}`) } catch { /* preserve fail-closed empty state */ }
      this.value = null
    }
  }

  private persist(): void {
    mkdirSync(dirname(this.path), { recursive: true })
    const tmp = `${this.path}.tmp`
    const payload = this.value === null ? null : this.safeStorage.encryptString(JSON.stringify(this.value)).toString('base64')
    writeFileSync(tmp, JSON.stringify({ schema_version: 1, ciphertext: payload }), { mode: 0o600 })
    renameSync(tmp, this.path)
  }
}
