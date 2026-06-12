import type { TrackedJob } from './storage'
import type { JobRecord, JobState, PipelineEvent, StepName } from '../../app/src/shared/types'

/**
 * Pure job-presentation logic shared by the popup and the service worker
 * (ext-jobs spec): hydrate-then-stream merge (records are the source of
 * truth; the stream is an optimization scoped to the popup's lifetime),
 * the state-only badge (per review adjudication: no step-derived percent),
 * and the stateless alarm-poll evaluation the SW runs on every wake.
 */

export const TERMINAL_STATES: readonly JobState[] = ['done', 'failed', 'interrupted']

export function isTerminal(state: JobState): boolean {
  return TERMINAL_STATES.includes(state)
}

/** One popup row: the hydrated record plus the latest live-stream overlay. */
export interface JobView {
  record: JobRecord
  liveStep: StepName | null
  liveMessage: string | null
}

export function viewFromRecord(record: JobRecord): JobView {
  return { record, liveStep: null, liveMessage: null }
}

export interface ApplyEventResult {
  views: Map<string, JobView>
  /** Set when a terminal event arrived: the popup re-fetches that record. */
  refreshJobId: string | null
}

/**
 * Fold one SSE event into the view map. Job events carry `data.job_id`
 * (engine/jobs.py:316); pack events never do (per Q5) and are ignored, as
 * are events for jobs this extension is not tracking. Terminal events do
 * not mutate state locally — the record stays the source of truth, so the
 * caller re-fetches it (`refreshJobId`).
 */
export function applyEvent(
  views: ReadonlyMap<string, JobView>,
  event: PipelineEvent
): ApplyEventResult {
  const jobId = event.data['job_id']
  if (typeof jobId !== 'string') return { views: new Map(views), refreshJobId: null }
  const view = views.get(jobId)
  if (view === undefined) return { views: new Map(views), refreshJobId: null }
  if (event.kind === 'job_done' || event.kind === 'job_failed') {
    return { views: new Map(views), refreshJobId: jobId }
  }
  const next = new Map(views)
  next.set(jobId, {
    record:
      view.record.state === 'queued' && event.kind === 'step_started'
        ? { ...view.record, state: 'running' }
        : view.record,
    liveStep: event.step,
    liveMessage: event.message
  })
  return { views: next, refreshJobId: null }
}

// ---- badge (state-only, per review adjudication) -----------------------------

export interface BadgeSpec {
  text: string
  color: string
}

/**
 * State-priority badge over the tracked jobs: running beats queued; a
 * terminal state shows only until its notification fired (`notified`),
 * after which the badge clears.
 */
export function badgeForJobs(
  jobs: readonly { state: JobState; notified: boolean }[]
): BadgeSpec {
  if (jobs.some((j) => j.state === 'running')) return { text: 'RUN', color: '#3a5fb0' }
  if (jobs.some((j) => j.state === 'queued' || j.state === 'awaiting-confirmation')) {
    return { text: 'QUE', color: '#6b7280' }
  }
  if (jobs.some((j) => (j.state === 'failed' || j.state === 'interrupted') && !j.notified)) {
    return { text: 'ERR', color: '#b3261e' }
  }
  if (jobs.some((j) => j.state === 'done' && !j.notified)) return { text: 'OK', color: '#1e7d32' }
  return { text: '', color: '#6b7280' }
}

// ---- alarm poll (stateless across SW restarts) --------------------------------

/** Poll fetch outcome per tracked job: the record, a 404, or engine down. */
export type PolledRecord = JobRecord | 'missing' | 'unreachable'

export interface NotificationSpec {
  jobId: string
  title: string
  message: string
}

export interface PollOutcome {
  /** Tracked list to persist (notified flags set, missing jobs dropped). */
  tracked: TrackedJob[]
  notifications: NotificationSpec[]
  badge: BadgeSpec
  /** Keep the 30 s alarm while any tracked job is (or may be) non-terminal. */
  keepAlarm: boolean
}

/**
 * Evaluate one alarm wake: every input comes from storage + fresh record
 * fetches, every output is data — the SW wiring just executes it
 * (notifications, badge, storage write, alarm clear). Unreachable fetches
 * keep the alarm (the engine may come back); missing records (404) drop
 * the job from tracking.
 */
export function evaluatePoll(
  tracked: readonly TrackedJob[],
  records: ReadonlyMap<string, PolledRecord>
): PollOutcome {
  const nextTracked: TrackedJob[] = []
  const notifications: NotificationSpec[] = []
  const badgeInput: { state: JobState; notified: boolean }[] = []
  let anyPending = false
  for (const job of tracked) {
    const polled = records.get(job.id) ?? 'unreachable'
    if (polled === 'missing') continue
    if (polled === 'unreachable') {
      anyPending = true
      nextTracked.push(job)
      continue
    }
    let notified = job.notified
    if (isTerminal(polled.state) && !job.notified) {
      notifications.push(notificationFor(polled))
      notified = true
    }
    if (!isTerminal(polled.state)) anyPending = true
    nextTracked.push({ ...job, title: polled.title, notified })
    badgeInput.push({ state: polled.state, notified })
  }
  return {
    tracked: nextTracked,
    notifications,
    badge: badgeForJobs(badgeInput),
    keepAlarm: anyPending
  }
}

function notificationFor(record: JobRecord): NotificationSpec {
  const subject = record.title ?? record.source
  if (record.state === 'done') {
    return { jobId: record.id, title: 'Transcript ready', message: subject }
  }
  return {
    jobId: record.id,
    title: record.state === 'failed' ? 'Transcription failed' : 'Transcription interrupted',
    message: record.error === null ? subject : `${subject} — ${record.error.message}`
  }
}
