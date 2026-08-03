import { PremiumRequestError } from './transport'
import type { PremiumTransport, TokenPair } from './transport'

export interface DeviceFlowDeps {
  openExternal(url: string): Promise<void>
  sleep(milliseconds: number): Promise<void>
  now(): number
}

export class PremiumDeviceFlow {
  constructor(private readonly transport: PremiumTransport, private readonly deps: DeviceFlowDeps) {}

  async authorize(): Promise<TokenPair> {
    const started = await this.transport.startDeviceAuthorization()
    if (!this.transport.ownsExternalUrl(started.verification_uri) || started.expires_in < 1 || started.interval < 1) throw new Error('invalid device authorization')
    const deadline = this.deps.now() + started.expires_in * 1000
    await this.deps.openExternal(started.verification_uri)
    let interval = started.interval * 1000
    while (this.deps.now() < deadline) {
      await this.deps.sleep(Math.min(interval, deadline - this.deps.now()))
      if (this.deps.now() >= deadline) break
      try { return await this.transport.pollDeviceToken(started.device_code) }
      catch (error) {
        if (!(error instanceof PremiumRequestError)) throw error
        if (error.code === 'slow_down') { interval += 5000; continue }
        if (error.code !== 'authorization_pending') throw error
      }
    }
    throw new Error('device authorization expired')
  }
}
