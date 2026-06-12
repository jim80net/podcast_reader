import { localStore } from './storage'
import type { TrackedJob } from './storage'
import type { JobRecord } from '../../app/src/shared/types'

/**
 * Shared submit-side effects for the popup and the service worker: record
 * the job in the bounded tracked list and ensure the 30-second completion
 * poll alarm (ext-jobs spec; 0.5 min is Chrome's floor — hence
 * `minimum_chrome_version` 120).
 */

export const POLL_ALARM = 'podcast-reader-poll'
export const POLL_PERIOD_MINUTES = 0.5

export async function trackSubmission(record: JobRecord): Promise<TrackedJob[]> {
  const tracked = await localStore().trackJob({
    id: record.id,
    source: record.source,
    title: record.title,
    submitted_at: record.created_at,
    notified: false
  })
  await chrome.alarms.create(POLL_ALARM, { periodInMinutes: POLL_PERIOD_MINUTES })
  return tracked
}
