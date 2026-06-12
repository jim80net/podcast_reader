/**
 * Source-eligibility classification mirroring the engine's
 * `pipeline.classify_input` semantics (src/podcast_reader/pipeline.py):
 * YouTube URLs route to the captions path, other http(s) URLs to yt-dlp.
 * The third Python class (LOCAL_FILE — anything that is not an http(s)
 * URL) is `ineligible` here: the extension submits page URLs only, and
 * browser-internal pages (chrome://, about:, file:) have no audio source
 * the engine could fetch.
 */

export type SourceKind = 'youtube' | 'url' | 'ineligible'

// Mirror of pipeline.py _YT_URL_RE.
const YT_URL_RE = /youtube\.com\/|youtu\.be\//

export function classifySource(url: string | undefined): SourceKind {
  if (url === undefined) return 'ineligible'
  if (!url.startsWith('http://') && !url.startsWith('https://')) return 'ineligible'
  if (YT_URL_RE.test(url)) return 'youtube'
  return 'url'
}

/** Human label for the submit affordance ("Transcribe this YouTube video"). */
export function sourceLabel(kind: SourceKind): string {
  if (kind === 'youtube') return 'Transcribe this YouTube video'
  return 'Transcribe this page'
}
