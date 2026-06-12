import { describe, expect, it } from 'vitest'

import {
  applyPackEvent,
  cudaAdvisoryNeeded,
  defaultSelection,
  deriveWhisperDevice,
  formatBytes,
  hardwareSummary,
  installableNow,
  progressPercent,
  selectionInstalled,
  setupNeeded
} from './packs-store'
import type { HardwareInfo, PackState, PackStatus, PipelineEvent } from '../../shared/types'

function pack(partial: Partial<PackStatus> & { id: string }): PackStatus {
  return {
    kind: 'model',
    display_name: partial.id,
    size: 1000,
    state: 'not-installed',
    recommended: false,
    installed_version: null,
    progress: null,
    error: null,
    ...partial
  }
}

function event(kind: PipelineEvent['kind'], data: Record<string, unknown>): PipelineEvent {
  return { kind, step: null, message: '', data }
}

const winNvidia: HardwareInfo = {
  platform: 'win32',
  nvidia_gpu: true,
  gpu_names: ['GeForce RTX 4090']
}

describe('applyPackEvent', () => {
  const packs = [pack({ id: 'cuda-runtime', kind: 'runtime' }), pack({ id: 'model-small' })]

  it('passes job events through untouched', () => {
    const result = applyPackEvent(packs, event('step_started', { job_id: 'j1' }))
    expect(result).toEqual({ packs, isPackEvent: false, needsRefresh: false })
  })

  it('patches progress in place without forcing a refresh', () => {
    const result = applyPackEvent(
      packs,
      event('pack_progress', { pack_id: 'model-small', bytes: 250, total: 1000 })
    )
    expect(result.isPackEvent).toBe(true)
    expect(result.needsRefresh).toBe(false)
    expect(result.packs[1]).toMatchObject({
      state: 'installing',
      progress: { bytes: 250, total: 1000 }
    })
    expect(result.packs[0]).toBe(packs[0]) // untouched entries keep identity
  })

  it('patches pack_state immediately AND requests authoritative re-hydration', () => {
    const result = applyPackEvent(
      packs,
      event('pack_state', { pack_id: 'model-small', state: 'installed' })
    )
    expect(result.needsRefresh).toBe(true)
    expect(result.packs[1]).toMatchObject({ state: 'installed', progress: null, error: null })
  })

  it('carries the structured error on a failed pack_state', () => {
    const result = applyPackEvent(
      packs,
      event('pack_state', {
        pack_id: 'model-small',
        state: 'failed',
        error: { code: 'verification_failed', message: 'sha256 mismatch' }
      })
    )
    expect(result.packs[1]).toMatchObject({
      state: 'failed',
      error: { code: 'verification_failed', message: 'sha256 mismatch' }
    })
  })

  it('flags refresh for events about unknown packs', () => {
    const result = applyPackEvent(packs, event('pack_state', { pack_id: 'nope', state: 'failed' }))
    expect(result).toEqual({ packs, isPackEvent: true, needsRefresh: true })
  })

  it('flags refresh for malformed pack events instead of guessing', () => {
    const malformed = applyPackEvent(
      packs,
      event('pack_progress', { pack_id: 'model-small', bytes: 'x' })
    )
    expect(malformed.needsRefresh).toBe(true)
    expect(malformed.packs).toBe(packs)
  })
})

describe('setupNeeded / defaultSelection', () => {
  it('triggers on a recommended pack that still needs installing', () => {
    for (const state of ['not-installed', 'resumable', 'failed', 'incompatible'] as PackState[]) {
      expect(setupNeeded([pack({ id: 'p', recommended: true, state })]), state).toBe(true)
    }
  })

  it('stays quiet when recommended packs are installed, installing, or unavailable', () => {
    for (const state of ['installed', 'installing', 'unavailable'] as PackState[]) {
      expect(setupNeeded([pack({ id: 'p', recommended: true, state })]), state).toBe(false)
    }
    expect(setupNeeded([pack({ id: 'p', recommended: false, state: 'not-installed' })])).toBe(false)
  })

  it('pre-selects exactly the missing recommended packs', () => {
    const selection = defaultSelection([
      pack({ id: 'cuda-runtime', recommended: true, state: 'not-installed' }),
      pack({ id: 'model-large-v3', recommended: true, state: 'installed' }),
      pack({ id: 'model-tiny', recommended: false, state: 'not-installed' }),
      pack({ id: 'diarization', recommended: false, state: 'unavailable' })
    ])
    expect(selection).toEqual(new Set(['cuda-runtime']))
  })
})

describe('deriveWhisperDevice (per S4)', () => {
  const cudaAvailable = [pack({ id: 'cuda-runtime', kind: 'runtime', state: 'not-installed' })]
  const cudaUnavailable = [pack({ id: 'cuda-runtime', kind: 'runtime', state: 'unavailable' })]

  it('picks cuda iff Windows + NVIDIA with the CUDA pack registry-available', () => {
    expect(deriveWhisperDevice(winNvidia, cudaAvailable)).toBe('cuda')
  })

  it('picks cpu when the CUDA pack is registry-unavailable (e.g. macOS gate)', () => {
    expect(deriveWhisperDevice(winNvidia, cudaUnavailable)).toBe('cpu')
  })

  it('picks cpu without an NVIDIA GPU or off Windows', () => {
    expect(
      deriveWhisperDevice({ platform: 'win32', nvidia_gpu: false, gpu_names: [] }, cudaAvailable)
    ).toBe('cpu')
    expect(
      deriveWhisperDevice(
        { platform: 'linux', nvidia_gpu: true, gpu_names: ['RTX'] },
        cudaUnavailable
      )
    ).toBe('cpu')
  })
})

describe('cudaAdvisoryNeeded (per S4/Q2)', () => {
  it('advises when device is cuda and the pack is not usable', () => {
    for (const state of [
      'not-installed',
      'resumable',
      'installing',
      'incompatible',
      'failed'
    ] as PackState[]) {
      expect(cudaAdvisoryNeeded('cuda', [pack({ id: 'cuda-runtime', state })]), state).toBe(true)
    }
  })

  it('stays quiet when installed, unavailable, or the device is cpu', () => {
    expect(cudaAdvisoryNeeded('cuda', [pack({ id: 'cuda-runtime', state: 'installed' })])).toBe(
      false
    )
    expect(cudaAdvisoryNeeded('cuda', [pack({ id: 'cuda-runtime', state: 'unavailable' })])).toBe(
      false
    )
    expect(cudaAdvisoryNeeded('cpu', [pack({ id: 'cuda-runtime', state: 'failed' })])).toBe(false)
  })
})

describe('wizard helpers', () => {
  it('installableNow covers the re-download states (per S8)', () => {
    expect(installableNow('failed')).toBe(true)
    expect(installableNow('incompatible')).toBe(true)
    expect(installableNow('installed')).toBe(false)
    expect(installableNow('installing')).toBe(false)
    expect(installableNow('unavailable')).toBe(false)
  })

  it('selectionInstalled requires every selected pack installed, and a non-empty selection', () => {
    const packs = [
      pack({ id: 'a', state: 'installed' }),
      pack({ id: 'b', state: 'installing' })
    ]
    expect(selectionInstalled(packs, new Set(['a']))).toBe(true)
    expect(selectionInstalled(packs, new Set(['a', 'b']))).toBe(false)
    expect(selectionInstalled(packs, new Set())).toBe(false)
  })

  it('formats sizes in decimal units', () => {
    expect(formatBytes(3_090_835_702)).toBe('3.1 GB')
    expect(formatBytes(78_203_619)).toBe('78 MB')
    expect(formatBytes(2_249)).toBe('2 kB')
    expect(formatBytes(512)).toBe('512 B')
  })

  it('summarizes hardware for the wizard header', () => {
    expect(hardwareSummary(winNvidia)).toBe('Windows — NVIDIA GPU: GeForce RTX 4090')
    expect(hardwareSummary({ platform: 'darwin', nvidia_gpu: false, gpu_names: [] })).toBe(
      'macOS — no NVIDIA GPU detected'
    )
    expect(hardwareSummary({ platform: 'win32', nvidia_gpu: true, gpu_names: [] })).toBe(
      'Windows — NVIDIA GPU detected'
    )
  })

  it('derives progress percentages defensively', () => {
    expect(progressPercent({ bytes: 250, total: 1000 })).toBe(25)
    expect(progressPercent({ bytes: 2000, total: 1000 })).toBe(100)
    expect(progressPercent({ bytes: 1, total: 0 })).toBe(0)
    expect(progressPercent(null)).toBe(0)
  })
})
