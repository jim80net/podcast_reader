import { el } from '../dom'
import { extractEngineDetail } from '../engine-error'
import { deriveProgress, sortJobs, sourceLabel } from '../job-view'
import type { AppStore, ViewCleanup } from '../store'
import type { JobRecord } from '../../../shared/types'

/**
 * New view (app-views spec, tasks 4.4/4.5): paste-URL or drop-file
 * submission, awaiting-confirmation jobs with Run/Dismiss (the landing spot
 * for protocol-initiated jobs — nothing here auto-executes), and live
 * step-level progress from forwarded events on top of record hydration.
 * Failed jobs show the structured {code, message, hint}; interrupted jobs
 * offer one-click retry (a fresh submission for the same source).
 */
export function mountNew(container: HTMLElement, store: AppStore): ViewCleanup {
  const formError = el('p', { class: 'error-text', attrs: { role: 'alert' } })
  formError.hidden = true
  const urlInput = el('input', {
    attrs: {
      type: 'text',
      id: 'new-source',
      placeholder: 'https://… or drop an audio file anywhere on this page',
      autocomplete: 'off'
    }
  })
  const titleInput = el('input', {
    attrs: { type: 'text', id: 'new-title', placeholder: 'optional', autocomplete: 'off' }
  })
  const submitButton = el('button', { text: 'Transcribe', attrs: { type: 'submit' } })
  const form = el(
    'form',
    { class: 'new-form' },
    el(
      'div',
      { class: 'field' },
      el('label', { text: 'URL or file path', attrs: { for: 'new-source' } }),
      urlInput
    ),
    el(
      'div',
      { class: 'field' },
      el('label', { text: 'Title', attrs: { for: 'new-title' } }),
      titleInput
    ),
    submitButton,
    formError
  )

  const confirmSection = el('section', { class: 'job-section' })
  const jobsSection = el('section', { class: 'job-section' })
  const dropHint = el('div', { class: 'drop-hint', text: 'Drop to transcribe' })
  dropHint.hidden = true
  container.append(el('h2', { text: 'New transcript' }), form, confirmSection, jobsSection, dropHint)

  let disposed = false

  function showFormError(err: unknown): void {
    formError.textContent = extractEngineDetail(err)
    formError.hidden = false
  }

  async function submit(source: string, title: string | null): Promise<void> {
    formError.hidden = true
    submitButton.disabled = true
    try {
      const job = await window.api.submitJob({ source, title })
      if (disposed) return
      store.upsert(job)
      urlInput.value = ''
      titleInput.value = ''
    } catch (err) {
      if (!disposed) showFormError(err)
    } finally {
      submitButton.disabled = false
    }
  }

  form.addEventListener('submit', (event) => {
    event.preventDefault()
    const source = urlInput.value.trim()
    if (source === '') {
      formError.textContent = 'Enter a URL or file path first.'
      formError.hidden = false
      return
    }
    void submit(source, titleInput.value.trim() === '' ? null : titleInput.value.trim())
  })

  // Drop a local audio file anywhere on the view: the preload bridge resolves
  // the real filesystem path (webUtils.getPathForFile) and it submits by path.
  const onDragOver = (event: DragEvent): void => {
    if (event.dataTransfer?.types.includes('Files') !== true) return
    event.preventDefault()
    dropHint.hidden = false
  }
  const onDragLeave = (event: DragEvent): void => {
    if (event.relatedTarget === null) dropHint.hidden = true
  }
  const onDrop = (event: DragEvent): void => {
    const file = event.dataTransfer?.files[0]
    if (file === undefined) return
    event.preventDefault()
    dropHint.hidden = true
    const path = window.api.getPathForFile(file)
    if (path === '') {
      formError.textContent = 'Could not resolve a filesystem path for the dropped file.'
      formError.hidden = false
      return
    }
    void submit(path, null)
  }
  container.addEventListener('dragover', onDragOver)
  container.addEventListener('dragleave', onDragLeave)
  container.addEventListener('drop', onDrop)

  function renderJobs(): void {
    const jobs = sortJobs([...store.jobs.values()])
    renderConfirmations(jobs.filter((job) => job.state === 'awaiting-confirmation'))
    renderActivity(jobs.filter((job) => job.state !== 'awaiting-confirmation'))
  }

  function renderConfirmations(pending: JobRecord[]): void {
    confirmSection.replaceChildren()
    if (pending.length === 0) return
    confirmSection.append(el('h3', { text: 'Waiting for your go-ahead' }))
    for (const job of pending) {
      const error = el('p', { class: 'error-text', attrs: { role: 'alert' } })
      error.hidden = true
      const run = el('button', { text: 'Run', attrs: { type: 'button' } })
      const dismiss = el('button', {
        text: 'Dismiss',
        class: 'button-secondary',
        attrs: { type: 'button' }
      })
      run.addEventListener('click', () => {
        run.disabled = dismiss.disabled = true
        window.api
          .confirmJob(job.id)
          .then((record) => store.upsert(record))
          .catch((err: unknown) => {
            error.textContent = extractEngineDetail(err)
            error.hidden = false
            run.disabled = dismiss.disabled = false
          })
      })
      dismiss.addEventListener('click', () => {
        run.disabled = dismiss.disabled = true
        window.api
          .dismissJob(job.id)
          .then(() => store.remove(job.id))
          .catch((err: unknown) => {
            error.textContent = extractEngineDetail(err)
            error.hidden = false
            run.disabled = dismiss.disabled = false
          })
      })
      confirmSection.append(
        el(
          'div',
          { class: 'card job-card confirm-card' },
          el('p', { class: 'job-source', text: job.source }),
          el('div', { class: 'job-actions' }, run, dismiss),
          error
        )
      )
    }
  }

  function renderActivity(jobs: JobRecord[]): void {
    jobsSection.replaceChildren()
    if (jobs.length === 0) return
    jobsSection.append(el('h3', { text: 'Jobs' }))
    for (const job of jobs) jobsSection.append(jobCard(job))
  }

  function jobCard(job: JobRecord): HTMLElement {
    const card = el(
      'div',
      { class: 'card job-card' },
      el(
        'div',
        { class: 'job-head' },
        el('span', { class: 'job-source', text: job.title ?? sourceLabel(job.source) }),
        el('span', { class: 'badge', text: job.state, attrs: { 'data-state': job.state } })
      )
    )
    const progress = deriveProgress(job.events)
    if (progress.steps.length > 0) {
      const list = el('ul', { class: 'step-list' })
      for (const step of progress.steps) {
        const item = el(
          'li',
          { class: 'step', attrs: { 'data-status': step.status } },
          el('span', { class: 'step-name', text: step.step }),
          el('span', { class: 'step-detail', text: step.detail })
        )
        for (const warning of step.warnings) {
          item.append(el('p', { class: 'step-warning', text: `⚠ ${warning}` }))
        }
        list.append(item)
      }
      card.append(list)
    }
    for (const warning of progress.warnings) {
      card.append(el('p', { class: 'step-warning', text: `⚠ ${warning}` }))
    }
    if (job.state === 'failed' && job.error !== null) {
      card.append(
        el(
          'div',
          { class: 'job-error', attrs: { role: 'alert' } },
          el('p', { class: 'job-error-message', text: `${job.error.code}: ${job.error.message}` }),
          job.error.hint !== '' ? el('p', { class: 'job-error-hint', text: job.error.hint }) : ''
        )
      )
    }
    if (job.state === 'interrupted') {
      const retry = el('button', { text: 'Retry', attrs: { type: 'button' } })
      retry.addEventListener('click', () => {
        retry.disabled = true
        void submit(job.source, job.title)
      })
      card.append(
        el(
          'div',
          { class: 'job-actions' },
          el('span', { class: 'job-error-hint', text: 'Interrupted by an engine shutdown.' }),
          retry
        )
      )
    }
    return card
  }

  renderJobs()
  const unsubscribeStore = store.subscribe(renderJobs)

  return () => {
    disposed = true
    unsubscribeStore()
    container.removeEventListener('dragover', onDragOver)
    container.removeEventListener('dragleave', onDragLeave)
    container.removeEventListener('drop', onDrop)
  }
}
