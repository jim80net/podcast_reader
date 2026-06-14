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

/** True when `event` announces the given source's media became ready. */
export function isMediaReady(event: PipelineEvent, sourceId: string): boolean {
  return (
    event.kind === 'media_state' &&
    event.data['source_id'] === sourceId &&
    event.data['state'] === 'ready'
  )
}
