import { EngineClient, EngineRequestError } from './client'
import { evaluatePoll } from './jobs-view'
import { localStore } from './storage'
import { POLL_ALARM, trackSubmission } from './tracking'
import { classifySource } from './url-detect'
import type { BadgeSpec, PolledRecord } from './jobs-view'

/**
 * Service worker (ext-jobs spec): stateless by construction — every wake
 * reads `chrome.storage.local`, polls job records, writes results back, and
 * exits. It NEVER holds an `/v1/events` stream (the popup owns streaming,
 * scoped to its own lifetime) and survives termination at any point because
 * no correctness depends on in-memory state.
 *
 * Two wake sources: the context-menu click (submit `info.pageUrl` — no host
 * permission needed for it) and the 30 s completion-poll alarm.
 */

const MENU_ID = 'podcast-reader-transcribe'

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: MENU_ID,
    title: 'Transcribe with Podcast Reader',
    contexts: ['page']
  })
})

chrome.contextMenus.onClicked.addListener((info) => {
  if (info.menuItemId === MENU_ID) void submitFromMenu(info.pageUrl)
})

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === POLL_ALARM) void pollOnce()
})

async function submitFromMenu(pageUrl: string | undefined): Promise<void> {
  if (pageUrl === undefined || classifySource(pageUrl) === 'ineligible') return
  const pairing = await localStore().pairing()
  if (pairing === null) return // pairing happens in the popup
  try {
    const record = await new EngineClient(pairing).submitJob(pageUrl)
    await trackSubmission(record)
    await pollOnce() // immediate badge update; the alarm takes over from here
  } catch {
    // No silent extension-side queuing or retrying (ext-jobs spec): the
    // popup is the surface that explains failures and offers the protocol
    // fallback.
  }
}

async function pollOnce(): Promise<void> {
  const store = localStore()
  const tracked = await store.trackedJobs()
  if (tracked.length === 0) {
    await chrome.alarms.clear(POLL_ALARM)
    await setBadge({ text: '', color: '#6b7280' })
    return
  }
  const pairing = await store.pairing()
  if (pairing === null) {
    await chrome.alarms.clear(POLL_ALARM)
    return
  }
  const client = new EngineClient(pairing)
  const records = new Map<string, PolledRecord>()
  for (const job of tracked) {
    try {
      records.set(job.id, await client.getJob(job.id))
    } catch (err) {
      records.set(
        job.id,
        err instanceof EngineRequestError && err.status === 404 ? 'missing' : 'unreachable'
      )
    }
  }
  const outcome = evaluatePoll(tracked, records)
  await store.setTrackedJobs(outcome.tracked)
  for (const spec of outcome.notifications) {
    chrome.notifications.create(`podcast-reader-${spec.jobId}`, {
      type: 'basic',
      iconUrl: 'icons/icon128.png',
      title: spec.title,
      message: spec.message
    })
  }
  await setBadge(outcome.badge)
  if (!outcome.keepAlarm) await chrome.alarms.clear(POLL_ALARM)
}

async function setBadge(badge: BadgeSpec): Promise<void> {
  await chrome.action.setBadgeText({ text: badge.text })
  if (badge.text !== '') await chrome.action.setBadgeBackgroundColor({ color: badge.color })
}
