import type { JobRecord, PipelineEvent, StepName } from '../../shared/types'

/**
 * Pure view-model derivation for job progress: the engine's event list
 * (record-hydrated plus live patches) becomes an ordered step timeline. Job
 * state itself comes from the record — `job_done`/`job_failed` events only
 * transition state in the store, so they carry nothing for the timeline.
 */

export interface StepView {
  step: StepName
  status: 'running' | 'done'
  /** The last non-empty message seen for this step. */
  detail: string
  warnings: string[]
}

export interface JobProgress {
  steps: StepView[]
  /** Warnings not attached to any step. */
  warnings: string[]
}

export function deriveProgress(events: readonly PipelineEvent[]): JobProgress {
  const steps: StepView[] = []
  const byStep = new Map<StepName, StepView>()
  const warnings: string[] = []

  const stepView = (step: StepName): StepView => {
    let view = byStep.get(step)
    if (view === undefined) {
      view = { step, status: 'running', detail: '', warnings: [] }
      byStep.set(step, view)
      steps.push(view)
    }
    return view
  }

  for (const event of events) {
    if (event.kind === 'job_done' || event.kind === 'job_failed') continue
    if (event.kind === 'warning') {
      if (event.step === null) warnings.push(event.message)
      else stepView(event.step).warnings.push(event.message)
      continue
    }
    const view = stepView(event.step as StepName)
    if (event.kind === 'step_finished') view.status = 'done'
    if (event.message !== '') view.detail = event.message
  }
  return { steps, warnings }
}

/** Newest-created first; non-mutating. */
export function sortJobs(jobs: readonly JobRecord[]): JobRecord[] {
  return [...jobs].sort((a, b) => b.created_at - a.created_at)
}

/** A compact human label for a job/library source (URL or local path). */
export function sourceLabel(source: string): string {
  try {
    const url = new URL(source)
    // Windows drive paths ("C:\…") parse as a URL with scheme "c:" — only
    // real web URLs take this branch; everything else is a local path.
    if (url.protocol === 'http:' || url.protocol === 'https:') {
      return `${url.host}${url.pathname === '/' ? '' : url.pathname}`
    }
  } catch {
    // not a URL: fall through to path handling
  }
  const basename = source.split(/[\\/]/).pop()
  return basename === undefined || basename === '' ? source : basename
}

/** Epoch seconds (engine timestamps) → local date string. */
export function formatDate(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toLocaleDateString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric'
  })
}
