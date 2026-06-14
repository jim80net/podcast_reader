import type { PipelineEvent } from '../../shared/types'

/**
 * Media-prep SSE event matching (media-playback spec, F4 wait-contract).
 *
 * `media_state` events carry `data.source_id` and `data.state`
 * (ready/preparing/unavailable) and NEVER a `job_id` (types.ts:23). The Reader
 * waits for the `ready` state for its own source_id before pointing the media
 * element at `app://media/<id>`; a missed event self-heals via a `mediaInfo`
 * re-fetch (the SSE consumer re-hydrates on every reconnect).
 */

/** The terminal state announced for a source, or null if `event` is unrelated. */
export function mediaTerminalState(
  event: PipelineEvent,
  sourceId: string
): 'ready' | 'unavailable' | null {
  if (event.kind !== 'media_state' || event.data['source_id'] !== sourceId) return null
  const state = event.data['state']
  if (state === 'ready' || state === 'unavailable') return state
  return null
}

/** True when `event` announces the given source's media became ready. */
export function isMediaReady(event: PipelineEvent, sourceId: string): boolean {
  return mediaTerminalState(event, sourceId) === 'ready'
}
