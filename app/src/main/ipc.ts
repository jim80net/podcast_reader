import { randomBytes } from 'node:crypto'

import { CHANNELS } from '../shared/ipc'
import type { EngineManager } from './engine-manager'
import type { PrivateWebController } from './private-web'
import type { SubmitJobRequest, UpdateStatus } from '../shared/ipc'
import type { PremiumAdSlot } from '../shared/ipc'
import type { SettingsUpdate } from '../shared/types'
import { disabledPremiumAccess } from './premium/controller'
import type { PremiumAccess } from './premium/controller'
import { publicPollResult, publicSubscription, subscriptionError, validateFeedUrlInput, validateSubscriptionId } from './subscriptions'
import { emailRequestError, publicEmailPreference, publicEmailStatus, validateEmailSourceId } from './email-control'
import type { EmailDeliverySummary } from '../shared/ipc'

/**
 * Main-process side of the typed IPC surface (design decision 4): each
 * renderer `invoke` maps to one `EngineClient` call. The bearer token never
 * crosses this boundary — responses carry engine payloads only.
 */

/** The subset of `ipcMain` used here (test seam). */
export interface IpcMainLike {
  // `any` matches electron's own listener signature, so both the real
  // ipcMain and unknown-typed test fakes are assignable.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  handle(channel: string, listener: (event: any, ...args: any[]) => unknown): void
}

/** The renderer-facing slice of the auto-updater (UpdaterController or the disabled gate). */
export interface UpdaterAccess {
  status(): UpdateStatus
  installNow(): Promise<void>
}

/** The renderer-facing slice of the app config (AppConfigStore). */
export interface AppConfigAccess {
  isFirstRunComplete(): boolean
  markFirstRunComplete(): void
}

export type PrivateWebAccess = Pick<PrivateWebController, 'status' | 'setEnabled'>

export function registerIpcHandlers(
  ipcMain: IpcMainLike,
  manager: EngineManager,
  updates: UpdaterAccess,
  config: AppConfigAccess,
  privateWeb?: PrivateWebAccess,
  premium: PremiumAccess = disabledPremiumAccess()
): void {
  const client = () => {
    const c = manager.client
    if (c === null) throw new Error('engine is not ready')
    return c
  }
  const manualEmailInFlight = new Map<string, Promise<EmailDeliverySummary>>()

  ipcMain.handle(CHANNELS.engineGetStatus, () => manager.status)
  ipcMain.handle(CHANNELS.keysStorageMode, () => manager.keyStorageMode)

  ipcMain.handle(CHANNELS.jobsSubmit, (_e, req: SubmitJobRequest) =>
    client().submitJob({
      source: req.source,
      title: req.title ?? null,
      requires_confirmation: req.requiresConfirmation ?? false,
      overrides: req.overrides
    })
  )
  ipcMain.handle(CHANNELS.jobsList, () => client().listJobs())
  ipcMain.handle(CHANNELS.jobsGet, (_e, jobId: string) => client().getJob(jobId))
  ipcMain.handle(CHANNELS.jobsConfirm, (_e, jobId: string) => client().confirmJob(jobId))
  ipcMain.handle(CHANNELS.jobsDismiss, (_e, jobId: string) => client().discardJob(jobId))

  ipcMain.handle(CHANNELS.libraryList, () => client().listLibrary())
  ipcMain.handle(CHANNELS.librarySearch, (_e, query: string) => client().searchLibrary(query))
  ipcMain.handle(CHANNELS.libraryTranscript, (_e, sourceId: string) =>
    client().transcriptHtml(sourceId)
  )
  // Media metadata only — bytes load via the app:// scheme (media-protocol.ts),
  // never over IPC, so the token never reaches the renderer (app-shell spec).
  ipcMain.handle(CHANNELS.mediaInfo, (_e, sourceId: string) => client().mediaInfo(sourceId))
  // The loopback URL of the tokenless engine-hosted YouTube embed page. The
  // renderer loads it as an iframe src so the player gets a real http origin
  // (Error 152/153 fix); reuses the engine coordinates the app:// handler uses.
  ipcMain.handle(CHANNELS.youtubeEmbedUrl, (_e, videoId: string): string | null => {
    const engine = manager.media
    if (engine === null) return null
    return `${engine.baseUrl}/v1/embed/${encodeURIComponent(videoId)}`
  })

  ipcMain.handle(CHANNELS.settingsGet, () => client().getSettings())
  ipcMain.handle(CHANNELS.settingsPut, (_e, settings: SettingsUpdate) =>
    client().putSettings(settings)
  )

  // Key writes go through the manager: vault first, then push to the engine.
  ipcMain.handle(CHANNELS.keysPut, (_e, provider: string, apiKey: string) =>
    manager.putKey(provider, apiKey)
  )
  ipcMain.handle(CHANNELS.keysTest, (_e, provider: string, apiKey?: string) =>
    client().testKey(provider, apiKey)
  )
  ipcMain.handle(CHANNELS.providersList, () => client().listProviders())

  ipcMain.handle(CHANNELS.packsList, () => client().listPacks())
  ipcMain.handle(CHANNELS.packsInstall, (_e, packId: string) => client().installPack(packId))
  ipcMain.handle(CHANNELS.packsUninstall, (_e, packId: string) => client().uninstallPack(packId))

  // Extension pairing: mint engine-side, compose with the port so Settings
  // can show the combined <port>-<code> paste string (design decision 11).
  // The code crosses this bridge exactly once, render-bound — never logged.
  ipcMain.handle(CHANNELS.pairStart, async () => {
    const minted = await client().mintPairing()
    const port = manager.port
    if (port === null) throw new Error('engine is not ready')
    return { port, code: minted.code, expires_at: minted.expires_at }
  })

  // Cookie jars: metadata listing + delete only — jar content has no IPC path.
  ipcMain.handle(CHANNELS.cookiesList, () => client().listCookieJars())
  ipcMain.handle(CHANNELS.cookiesDelete, (_e, domain: string) => client().deleteCookieJar(domain))

  // First-run flag (setup wizard gate) — app-side state, no engine involved.
  ipcMain.handle(CHANNELS.firstRunGet, () => config.isFirstRunComplete())
  ipcMain.handle(CHANNELS.firstRunComplete, () => config.markFirstRunComplete())
  ipcMain.handle(CHANNELS.privateWebGetStatus, () => privateWeb?.status ?? { state: 'disabled' })
  ipcMain.handle(CHANNELS.privateWebSetEnabled, (_e, enabled: boolean) => {
    if (privateWeb === undefined) throw new Error('private web access is unavailable')
    return privateWeb.setEnabled(enabled)
  })
  ipcMain.handle(CHANNELS.premiumGetState, () => premium.state())
  ipcMain.handle(CHANNELS.premiumConnect, () => premium.connect())
  ipcMain.handle(CHANNELS.premiumSignOut, () => premium.signOut())
  ipcMain.handle(CHANNELS.premiumInventory, (_e, slot: PremiumAdSlot) => premium.inventory(slot))
  ipcMain.handle(CHANNELS.premiumOpenCta, (_e, slot: PremiumAdSlot, url: string) => premium.openCta(slot, url))
  ipcMain.handle(CHANNELS.subscriptionsList, async () => {
    try { return (await client().listSubscriptions()).map(publicSubscription) }
    catch (error) { throw subscriptionError(error) }
  })
  ipcMain.handle(CHANNELS.subscriptionsCreate, async (_e, feedUrl: unknown) => {
    if (!premium.subscriptionsEnabled()) throw new Error('premium_feature_unavailable')
    try { return publicSubscription(await client().createSubscription(validateFeedUrlInput(feedUrl))) }
    catch (error) { throw subscriptionError(error) }
  })
  ipcMain.handle(CHANNELS.subscriptionsPoll, async (_e, subscriptionId: unknown) => {
    if (!premium.subscriptionsEnabled()) throw new Error('premium_feature_unavailable')
    try { return publicPollResult(await client().pollSubscription(validateSubscriptionId(subscriptionId))) }
    catch (error) { throw subscriptionError(error) }
  })
  ipcMain.handle(CHANNELS.subscriptionsDelete, async (_e, subscriptionId: unknown) => {
    if (!premium.subscriptionsEnabled()) throw new Error('premium_feature_unavailable')
    try { await client().deleteSubscription(validateSubscriptionId(subscriptionId)) }
    catch (error) { throw subscriptionError(error) }
  })
  ipcMain.handle(CHANNELS.emailPreferenceGet, async (_e, subscriptionId: unknown) => {
    const id = validateSubscriptionId(subscriptionId)
    const subject = premium.emailSubject()
    if (subject === null) return { subscriptionId: id, enabled: false, consentRevision: 0 }
    try { return publicEmailPreference(await client().getEmailPreference(id, subject)) }
    catch (error) { throw emailRequestError(error) }
  })
  ipcMain.handle(CHANNELS.emailPreferenceSet, async (_e, subscriptionId: unknown, enabled: unknown) => {
    const id = validateSubscriptionId(subscriptionId)
    if (typeof enabled !== 'boolean') throw new Error('invalid_email_preference')
    if (enabled && !premium.emailEnabled()) throw new Error('premium_feature_unavailable')
    const subject = premium.emailSubject()
    if (subject === null) throw new Error('email_account_unavailable')
    try {
      const result = publicEmailPreference(await client().setEmailPreference(id, subject, enabled))
      if (enabled) premium.wakeEmailSender()
      return result
    } catch (error) { throw emailRequestError(error) }
  })
  ipcMain.handle(CHANNELS.emailManualCreate, (_e, sourceId: unknown) => {
    if (!premium.emailEnabled()) throw new Error('premium_feature_unavailable')
    const source = validateEmailSourceId(sourceId)
    const existing = manualEmailInFlight.get(source)
    if (existing !== undefined) return existing
    const actionId = `act_${randomBytes(18).toString('base64url')}`
    const operation = client().createManualEmail(actionId, source)
      .then((value) => {
        const result = publicEmailStatus(value)
        premium.wakeEmailSender()
        return result
      })
      .catch((error: unknown) => { throw emailRequestError(error) })
      .finally(() => {
        if (manualEmailInFlight.get(source) === operation) manualEmailInFlight.delete(source)
      })
    manualEmailInFlight.set(source, operation)
    return operation
  })
  ipcMain.handle(CHANNELS.emailOutboxList, async () => {
    if (premium.emailSubject() === null) return []
    try { return (await client().listEmailOutbox()).map(publicEmailStatus) }
    catch (error) { throw emailRequestError(error) }
  })

  ipcMain.handle(CHANNELS.updateGetStatus, () => updates.status())
  ipcMain.handle(CHANNELS.updateInstall, () => updates.installNow())

  // Manual recovery from the terminal `failed` state — respawns a fresh engine
  // without going through the quit sequence (engine-respawn-supervision design).
  ipcMain.handle(CHANNELS.engineRestart, () => manager.restart())
}
