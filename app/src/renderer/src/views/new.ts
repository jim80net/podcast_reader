import { el } from '../dom'
import { extractEngineDetail } from '../engine-error'
import { deriveProgress, sortJobs, sourceLabel } from '../job-view'
import { LatestGate } from '../latest-gate'
import { buildRerunOverrides } from '../rerun-plan'
import { hrefFor } from '../router'
import type { AppStore, ViewCleanup } from '../store'
import type { JobOverrides, JobRecord } from '../../../shared/types'

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
    class: 'new-source-input',
    attrs: {
      type: 'text',
      id: 'new-source',
      placeholder: 'Paste a URL (YouTube, X, podcast…) — or drop a file anywhere on this page',
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
  const dropHint = el('div', { class: 'drop-hint', text: 'Drop an audio or video file' })
  dropHint.hidden = true
  container.append(el('h2', { text: 'New transcript' }), form, confirmSection, jobsSection, dropHint)

  let disposed = false
  let rerunOpening = false // guards the rerun dialog's pre-append async gap
  // source → its library entry (id + title), so a finished job can show the
  // real video title as its header and link straight to the transcript (the
  // JobRecord carries neither). Refreshed on job_done, like the Library view.
  let transcriptBySource = new Map<string, { id: string; title: string }>()
  // Several job_done events can fire a burst of loads; the gate drops all but
  // the latest response so a slow one can't clobber a newer map (OCR).
  const libraryGate = new LatestGate()
  async function loadLibrary(): Promise<void> {
    const isLatest = libraryGate.next()
    try {
      const entries = await window.api.listLibrary()
      if (disposed || !isLatest()) return
      transcriptBySource = new Map(entries.map((e) => [e.source, { id: e.source_id, title: e.title }]))
      renderJobs()
    } catch {
      // Transient/older engine: done jobs simply won't show the link yet. Silent
      // by design (matches the Settings/setup degrade pattern; the renderer
      // fences console) — it self-heals on the next job_done or remount.
    }
  }

  function showFormError(err: unknown): void {
    formError.textContent = extractEngineDetail(err)
    formError.hidden = false
  }

  async function submit(
    source: string,
    title: string | null,
    overrides?: JobOverrides
  ): Promise<void> {
    formError.hidden = true
    submitButton.disabled = true
    try {
      const job = await window.api.submitJob({ source, title, overrides })
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
    const lib = transcriptBySource.get(job.source)
    const card = el('div', { class: 'card job-card' })

    // Header = the video title (from the library entry once done; else the
    // job's title or the source). When done, the title IS the link to the
    // transcript (no separate "View transcript" link).
    const titleText = lib?.title ?? job.title ?? sourceLabel(job.source)
    const titleEl =
      lib !== undefined
        ? el('a', {
            class: 'job-title',
            text: titleText,
            attrs: { href: hrefFor({ view: 'reader', sourceId: lib.id }) }
          })
        : el('span', { class: 'job-title', text: titleText })
    card.append(
      el(
        'div',
        { class: 'job-head' },
        titleEl,
        el('span', { class: 'badge', text: job.state, attrs: { 'data-state': job.state } })
      ),
      // The full source URL on the next row down.
      el('p', { class: 'job-source-full', text: job.source })
    )

    // Step status as a 2-column table. The trivial `resolve`/`download` steps
    // are hidden; `render` shows only when it warned/errored.
    const progress = deriveProgress(job.events)
    const visibleSteps = progress.steps.filter((s) => {
      if (s.step === 'resolve' || s.step === 'download') return false
      if (s.step === 'render') return s.warnings.length > 0
      return true
    })
    const table = el('div', { class: 'job-rows' })
    for (const step of visibleSteps) {
      table.append(
        el('span', { class: 'job-row-key', text: step.step }),
        el('span', { class: 'job-row-val', attrs: { 'data-status': step.status } }, ...[
          el('span', { text: step.detail }),
          ...step.warnings.map((w) => el('span', { class: 'step-warning', text: `⚠ ${w}` }))
        ])
      )
    }
    // Model rows (Transcription + Chapters) — what the job actually ran with.
    if (job.models !== null) {
      const usedCaptions = progress.steps.some((s) => s.step === 'captions')
      const chapterModel = job.models.chapter_model
      table.append(
        el('span', { class: 'job-row-key', text: 'transcription' }),
        el('span', {
          class: 'job-row-val',
          text: usedCaptions ? 'YouTube captions' : job.models.whisper_model
        }),
        el('span', { class: 'job-row-key', text: 'chapters' }),
        el('span', {
          class: 'job-row-val',
          text:
            job.models.chapter_provider + (chapterModel !== '' ? ` · ${chapterModel}` : ' · default')
        })
      )
    }
    if (table.childElementCount > 0) card.append(table)

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
    // Rerun is a plain link beside the model rows (finished/failed jobs).
    if (job.state === 'done' || job.state === 'failed') {
      const rerun = el('a', {
        class: 'button-link job-rerun',
        text: 'Rerun with a different model…',
        attrs: { href: '#', role: 'button' }
      })
      rerun.addEventListener('click', (event) => {
        event.preventDefault()
        void openRerunDialog(job)
      })
      card.append(rerun)
    }
    return card
  }

  // Rerun dialog: two opt-in sections (re-transcribe with a Whisper model /
  // regenerate chapters with a provider+model), prefilled from current settings.
  // Submitting resubmits the same source with the chosen overrides; the engine
  // clears exactly the cached artifacts the change invalidates.
  async function openRerunDialog(job: JobRecord): Promise<void> {
    // One dialog at a time, including during the async settings/providers fetch
    // (the in-DOM dialog guards after append; the flag guards the gap before it).
    if (rerunOpening || container.querySelector('.rerun-dialog') !== null) return
    rerunOpening = true
    let settings: Awaited<ReturnType<typeof window.api.getSettings>>
    let providers: Awaited<ReturnType<typeof window.api.listProviders>>
    try {
      ;[settings, providers] = await Promise.all([
        window.api.getSettings(),
        window.api.listProviders()
      ])
    } catch (err) {
      rerunOpening = false
      if (!disposed) showFormError(err)
      return
    }
    rerunOpening = false
    if (disposed) return

    const whisperCheck = el('input', { attrs: { type: 'checkbox', id: 'rerun-whisper' } })
    const whisperModel = el('input', {
      attrs: { type: 'text', value: settings.whisper_model, disabled: '' }
    })
    whisperCheck.addEventListener('change', () => {
      whisperModel.disabled = !whisperCheck.checked
    })

    const chapterCheck = el('input', { attrs: { type: 'checkbox', id: 'rerun-chapter' } })
    const providerSelect = el('select', { attrs: { disabled: '' } })
    for (const p of providers) {
      providerSelect.append(el('option', { text: p.id, attrs: { value: p.id } }))
    }
    providerSelect.value = settings.chapter_provider
    const chapterModel = el('input', {
      attrs: { type: 'text', value: settings.chapter_model, placeholder: 'provider default', disabled: '' }
    })
    const customUrl = el('input', {
      attrs: { type: 'text', value: settings.custom_provider_url, placeholder: 'https://…', disabled: '' }
    })
    const customField = el(
      'div',
      { class: 'field' },
      el('label', { text: 'Custom base URL' }),
      customUrl
    )
    const syncChapter = (): void => {
      const on = chapterCheck.checked
      providerSelect.disabled = chapterModel.disabled = !on
      customUrl.disabled = !on
      customField.hidden = providerSelect.value !== 'custom'
    }
    chapterCheck.addEventListener('change', syncChapter)
    providerSelect.addEventListener('change', syncChapter)
    syncChapter()

    const error = el('p', { class: 'error-text', attrs: { role: 'alert' } })
    error.hidden = true
    const cancel = el('button', { class: 'button-secondary', text: 'Cancel', attrs: { type: 'button' } })
    const run = el('button', { text: 'Rerun', attrs: { type: 'submit' } })
    const dialog = el(
      'dialog',
      { class: 'rerun-dialog' },
      el(
        'form',
        { attrs: { method: 'dialog' } },
        el('h3', { text: 'Rerun with a different model' }),
        el('p', { class: 'rerun-target', text: job.title ?? job.source }),
        el(
          'div',
          { class: 'field rerun-section' },
          el('label', {}, whisperCheck, document.createTextNode(' Re-transcribe the audio')),
          el('label', { text: 'Whisper model' }),
          whisperModel,
          el('p', { class: 'field-note', text: 'Ignored for YouTube sources (they use captions).' })
        ),
        el(
          'div',
          { class: 'field rerun-section' },
          el('label', {}, chapterCheck, document.createTextNode(' Regenerate chapters')),
          el('label', { text: 'Provider' }),
          providerSelect,
          el('label', { text: 'Chapter model' }),
          chapterModel,
          customField
        ),
        error,
        el('div', { class: 'form-actions' }, run, cancel)
      )
    )

    // Detach on ANY close — the Cancel/submit paths and the native Esc-to-close
    // both fire 'close', so the dialog never lingers detached in the DOM.
    dialog.addEventListener('close', () => dialog.remove())
    const close = (): void => dialog.close()
    cancel.addEventListener('click', close)
    dialog.addEventListener('submit', (event) => {
      event.preventDefault()
      const plan = buildRerunOverrides({
        reTranscribe: whisperCheck.checked,
        whisperModel: whisperModel.value,
        reChapter: chapterCheck.checked,
        chapterProvider: providerSelect.value,
        chapterModel: chapterModel.value,
        customUrl: customUrl.value
      })
      if (!plan.valid) {
        error.textContent = 'Enable at least one option (and set a Whisper model to re-transcribe).'
        error.hidden = false
        return
      }
      close()
      void submit(job.source, job.title, plan.overrides)
    })
    container.append(dialog)
    dialog.showModal()
  }

  renderJobs()
  void loadLibrary()
  const unsubscribeStore = store.subscribe(renderJobs)
  // A finishing job adds a library entry; refresh the source→transcript map so
  // its "View transcript" link appears without leaving the view.
  const unsubscribeEvents = window.api.onPipelineEvent((event) => {
    if (event.kind === 'job_done') void loadLibrary()
  })

  return () => {
    disposed = true
    unsubscribeStore()
    unsubscribeEvents()
    container.removeEventListener('dragover', onDragOver)
    container.removeEventListener('dragleave', onDragLeave)
    container.removeEventListener('drop', onDrop)
  }
}
