export class PremiumOrigin {
  private constructor(readonly value: string) {}

  static fromTrustedConfiguration(value: string): PremiumOrigin {
    if (value === '' || value !== value.trim() || value.length > 2048) throw new Error('invalid premium origin')
    const url = new URL(value)
    if (
      url.protocol !== 'https:' ||
      url.username !== '' ||
      url.password !== '' ||
      url.pathname !== '/' ||
      url.search !== '' ||
      url.hash !== '' ||
      url.hostname !== url.hostname.toLowerCase() ||
      url.hostname.endsWith('.') ||
      url.origin !== value
    ) throw new Error('invalid premium origin')
    return new PremiumOrigin(value)
  }

  resolve(path: string): string {
    if (!path.startsWith('/v1/')) throw new Error('invalid premium route')
    return `${this.value}${path}`
  }

  owns(url: string): boolean {
    try { const parsed = new URL(url); return parsed.origin === this.value && parsed.username === '' && parsed.password === '' } catch { return false }
  }

  toString(): string { return 'PremiumOrigin(redacted)' }
}
