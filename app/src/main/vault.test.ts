import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  writeFileSync
} from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'

import { KeyVault } from './vault'
import type { SafeStorageLike } from './vault'

/** Reversible non-identity transform: ciphertext never contains the plaintext. */
const fakeSafeStorage: SafeStorageLike = {
  isEncryptionAvailable: () => true,
  encryptString: (s) => Buffer.from(`enc:${Buffer.from(s).reverse().toString('hex')}`),
  decryptString: (b) => {
    const hex = b.toString().replace(/^enc:/, '')
    return Buffer.from(hex, 'hex').reverse().toString()
  }
}

const noEncryption: SafeStorageLike = {
  isEncryptionAvailable: () => false,
  encryptString: () => {
    throw new Error('encryption unavailable')
  },
  decryptString: () => {
    throw new Error('encryption unavailable')
  }
}

let dirs: string[] = []

function tempVaultPath(): string {
  const dir = mkdtempSync(join(tmpdir(), 'vault-test-'))
  dirs.push(dir)
  return join(dir, 'vault.json')
}

afterEach(() => {
  for (const dir of dirs) rmSync(dir, { recursive: true, force: true })
  dirs = []
})

describe('KeyVault (encrypted mode)', () => {
  it('stores and returns keys, with no plaintext on disk', () => {
    const path = tempVaultPath()
    const vault = new KeyVault(path, fakeSafeStorage)
    vault.setKey('anthropic', 'sk-ant-supersecret')
    vault.setKey('openai', 'sk-oai-alsosecret')

    expect(vault.keys()).toEqual({ anthropic: 'sk-ant-supersecret', openai: 'sk-oai-alsosecret' })

    const onDisk = readFileSync(path, 'utf8')
    expect(onDisk).not.toContain('sk-ant-supersecret')
    expect(onDisk).not.toContain('sk-oai-alsosecret')
    expect(onDisk).not.toContain(Buffer.from('sk-ant-supersecret').toString('base64'))
  })

  it('round-trips through a fresh instance (persistence)', () => {
    const path = tempVaultPath()
    new KeyVault(path, fakeSafeStorage).setKey('anthropic', 'sk-123')
    const reloaded = new KeyVault(path, fakeSafeStorage)
    expect(reloaded.keys()).toEqual({ anthropic: 'sk-123' })
    expect(reloaded.providers()).toEqual(['anthropic'])
  })

  it('clearing a key (empty string) removes the entry', () => {
    const path = tempVaultPath()
    const vault = new KeyVault(path, fakeSafeStorage)
    vault.setKey('anthropic', 'sk-123')
    vault.setKey('anthropic', '')
    expect(vault.keys()).toEqual({})
    expect(new KeyVault(path, fakeSafeStorage).keys()).toEqual({})
  })

  it('reports encrypted mode', () => {
    expect(new KeyVault(tempVaultPath(), fakeSafeStorage).mode).toBe('encrypted')
  })

  it('quarantines a corrupt vault file instead of letting the next save clobber it', () => {
    const path = tempVaultPath()
    writeFileSync(path, 'not json{', 'utf8')
    const logs: string[] = []
    const vault = new KeyVault(path, fakeSafeStorage, (m) => logs.push(m))

    expect(vault.keys()).toEqual({})
    expect(existsSync(path)).toBe(false) // moved aside, not left in place
    const quarantined = readdirSync(dirname(path)).filter((f) => f.includes('vault.json.corrupt-'))
    expect(quarantined).toHaveLength(1)
    expect(readFileSync(join(dirname(path), quarantined[0] ?? ''), 'utf8')).toBe('not json{')
    expect(logs.join('\n')).toContain('quarantined')

    // a later save writes a fresh vault and leaves the quarantined copy alone
    vault.setKey('anthropic', 'sk-123')
    expect(new KeyVault(path, fakeSafeStorage).keys()).toEqual({ anthropic: 'sk-123' })
    expect(readFileSync(join(dirname(path), quarantined[0] ?? ''), 'utf8')).toBe('not json{')
  })

  it('quarantines an unreadable-but-present vault path (read error other than ENOENT)', () => {
    const path = tempVaultPath()
    mkdirSync(path) // readFileSync(path) -> EISDIR: present but unreadable
    const logs: string[] = []
    const vault = new KeyVault(path, fakeSafeStorage, (m) => logs.push(m))
    expect(vault.keys()).toEqual({})
    expect(logs.join('\n')).toContain('quarantine')
  })

  it('treats a missing vault file as fresh — no quarantine, no log', () => {
    const path = tempVaultPath()
    const logs: string[] = []
    expect(new KeyVault(path, fakeSafeStorage, (m) => logs.push(m)).keys()).toEqual({})
    expect(logs).toEqual([])
    expect(readdirSync(dirname(path))).toEqual([])
  })

  it('drops undecryptable entries instead of crashing', () => {
    const path = tempVaultPath()
    const vault = new KeyVault(path, fakeSafeStorage)
    vault.setKey('anthropic', 'sk-123')
    // a vault written under another OS keychain state decrypts to garbage/throws
    const broken = new KeyVault(path, {
      ...fakeSafeStorage,
      decryptString: () => {
        throw new Error('bad key')
      }
    })
    expect(broken.keys()).toEqual({})
  })
})

describe('KeyVault (session-memory mode, per design decision 5)', () => {
  it('never writes the vault file when encryption is unavailable', () => {
    const path = tempVaultPath()
    const vault = new KeyVault(path, noEncryption)
    vault.setKey('anthropic', 'sk-123')
    expect(vault.mode).toBe('session-memory')
    expect(vault.keys()).toEqual({ anthropic: 'sk-123' })
    expect(existsSync(path)).toBe(false)
  })

  it('keys are session-only (a fresh instance sees nothing)', () => {
    const path = tempVaultPath()
    new KeyVault(path, noEncryption).setKey('anthropic', 'sk-123')
    expect(new KeyVault(path, noEncryption).keys()).toEqual({})
  })
})
