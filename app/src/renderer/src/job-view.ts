import { isJobPipelineEvent } from '../../shared/types'
import type { JobError, JobRecord, JobState, PipelineEvent, StepName } from '../../shared/types'

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
}

export interface JobWarningView {
  message: string
  technicalDetail: string | null
}

export interface JobErrorView {
  cause: string
  recovery: string
  technicalDetail: string
}

const completedStepCopy: Record<StepName, string> = {
  resolve: 'Source resolved.',
  captions: 'Captions loaded.',
  download: 'Download complete.',
  transcribe: 'Transcription complete.',
  diarize: 'Speaker detection complete.',
  chapters: 'Chapter step complete.',
  render: 'Transcript ready.'
}

/** Lead with an actionable summary while retaining exact diagnostics on demand. */
export function userFacingJobError(error: JobError): JobErrorView {
  let cause = 'The job could not be completed.'
  let recovery = error.hint.trim() || 'Check the source and try again.'

  if (/transcript|caption/i.test(`${error.code} ${error.message}`)) {
    cause = 'This video is unavailable or does not provide captions.'
    recovery = 'Check that the video is public and has captions, then try again.'
  } else if (/download/i.test(`${error.code} ${error.message}`)) {
    cause = 'The audio could not be downloaded from this source.'
  }

  const technicalParts = [`Code: ${error.code}`, `Message:\n${error.message}`]
  if (error.detail.trim() !== '') technicalParts.push(`Details:\n${error.detail}`)
  return { cause, recovery, technicalDetail: technicalParts.join('\n\n') }
}

/** Keep implementation vocabulary out of the primary completion path. */
export function userFacingJobWarning(warning: string): JobWarningView {
  if (/ANTHROPIC_API_KEY|chapter.{0,24}(?:provider|API key)|no API key/i.test(warning)) {
    return {
      message: 'Chapters are off. Add a chapter provider in Settings to enable them.',
      technicalDetail: warning
    }
  }
  return { message: warning, technicalDetail: null }
}

export function deriveProgress(events: readonly PipelineEvent[], jobState?: JobState): JobProgress {
  const steps: StepView[] = []
  const byStep = new Map<StepName, StepView>()
  const terminalMessageSteps = new Set<StepName>()

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
    if (!isJobPipelineEvent(event)) continue
    if (event.kind === 'job_done' || event.kind === 'job_failed') continue
    if (event.kind === 'warning') {
      stepView(event.step).warnings.push(event.message)
      continue
    }
    const view = stepView(event.step)
    if (event.kind === 'step_finished') {
      view.status = 'done'
      if (event.message !== '') {
        terminalMessageSteps.add(event.step)
      } else {
        view.detail = completedStepCopy[event.step]
      }
    }
    if (event.message !== '') view.detail = event.message
  }
  if (jobState === 'done') {
    for (const view of steps) {
      view.status = 'done'
      if (!terminalMessageSteps.has(view.step)) view.detail = completedStepCopy[view.step]
    }
  }
  return { steps }
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
