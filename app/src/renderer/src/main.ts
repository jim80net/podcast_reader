/**
 * Minimal renderer shell (groups 2–3): proves the supervision + IPC stack
 * end-to-end by rendering engine status, current state via job-record
 * hydration, and the live forwarded event stream. The real four views are
 * group 4 (tasks 4.1–4.6).
 */

import type { EngineStatus } from '../../shared/ipc'
import type { JobRecord, PipelineEvent } from '../../shared/types'

const statusEl = document.getElementById('status') as HTMLSpanElement
const detailEl = document.getElementById('detail') as HTMLDivElement
const logEl = document.getElementById('event-log') as HTMLUListElement

function renderStatus(status: EngineStatus): void {
  statusEl.dataset['state'] = status.state
  switch (status.state) {
    case 'starting':
      statusEl.textContent = 'starting…'
      break
    case 'ready':
      statusEl.textContent = `ready — v${status.version} on 127.0.0.1:${status.port} ` +
        `(pid ${status.pid}, ${status.adopted ? 'adopted' : 'spawned'})`
      break
    case 'failed':
      statusEl.textContent = 'failed'
      detailEl.textContent = status.message
      break
    case 'stopped':
      statusEl.textContent = 'stopped'
      break
  }
}

function appendLog(text: string): void {
  const item = document.createElement('li')
  item.textContent = `${new Date().toLocaleTimeString()} ${text}`
  logEl.appendChild(item)
  logEl.scrollTop = logEl.scrollHeight
}

function renderHydration(jobs: JobRecord[]): void {
  const counts = new Map<string, number>()
  for (const job of jobs) counts.set(job.state, (counts.get(job.state) ?? 0) + 1)
  const summary =
    jobs.length === 0
      ? 'no jobs'
      : [...counts.entries()].map(([state, n]) => `${state}: ${n}`).join(', ')
  detailEl.textContent = `jobs (hydrated from records): ${summary}`
}

window.api.onEngineStatus(renderStatus)
window.api.onJobsHydrated((jobs) => {
  renderHydration(jobs)
  appendLog(`hydrated ${jobs.length} job record(s)`)
})
window.api.onPipelineEvent((event: PipelineEvent) => {
  appendLog(`[${event.kind}] ${event.step ?? '-'} ${event.message}`)
})
window.api.onProtocolRequest((job) => {
  appendLog(`protocol request awaiting confirmation: ${job.source} (job ${job.id})`)
})

// Catch up on anything broadcast before this script attached.
void window.api.getEngineStatus().then(renderStatus)
