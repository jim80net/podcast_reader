import { createHash } from 'node:crypto'

import { expect, expectEngineState, test } from './fixtures'
import type { Harness } from './fixtures'

/**
 * Floating media player e2e (task 9.2, media-playback spec) against the mock
 * engine's /v1/media routes. Asserts the player mounts per kind, that clicking
 * a transcript passage seeks the player, and that a player time update
 * highlights/follows the matching passage — the full bidirectional pr-sync
 * bridge, exercised without real media decoding.
 */

// A 64-char sha256 hex id (the app:// handler rejects anything else; the mock
// keys media by source_id, but the Reader is opened by the library card's id).
const VIDEO_ID = createHash('sha256').update('video-entry').digest('hex')
const AUDIO_ID = createHash('sha256').update('audio-entry').digest('hex')

/** A minimal transcript carrying the artifact's data-start passages + sync script. */
function transcriptHtml(): string {
  return `<!DOCTYPE html><html><head><style>.sync-active{background:#ffd}</style></head><body>
    <p class="passage" data-start="0.000" data-end="5.000">first passage</p>
    <p class="passage" data-start="5.000" data-end="10.000">second passage</p>
    <p class="passage" data-start="10.000" data-end="15.000">third passage</p>
    <script>
    (function(){
      if (window.parent === window) return;
      var CH='pr-sync';
      var items=[].slice.call(document.querySelectorAll('[data-start]')).map(function(el){
        return {el:el,start:parseFloat(el.getAttribute('data-start'))};
      }).filter(function(it){return !isNaN(it.start);});
      items.sort(function(a,b){return a.start-b.start;});
      items.forEach(function(it){
        it.el.style.cursor='pointer';
        it.el.addEventListener('click',function(){
          window.parent.postMessage({ch:CH,type:'seek',t:it.start},'*');
        });
      });
      var active=null;
      function highlight(t){
        var found=null;
        for(var i=0;i<items.length;i++){ if(items[i].start<=t){found=items[i];}else{break;} }
        if(found===active) return;
        if(active) active.el.classList.remove('sync-active');
        active=found;
        if(active) active.el.classList.add('sync-active');
      }
      window.addEventListener('message',function(e){
        var d=e.data; if(!d||d.ch!==CH) return;
        if(d.type==='time'&&typeof d.t==='number') highlight(d.t);
      });
      window.parent.postMessage({ch:CH,type:'ready'},'*');
    })();
    </script>
  </body></html>`
}

async function seedReader(harness: Harness, sourceId: string, kind: string): Promise<void> {
  await harness.mock.control('/seed', {
    library: [
      {
        source_id: sourceId,
        source: 'https://example.com/clip',
        title: `${kind} entry`,
        html_path: `/mock/${sourceId}.html`,
        created_at: 1_700_000_000
      }
    ],
    transcripts: { [sourceId]: transcriptHtml() },
    media: [{ source_id: sourceId, kind }]
  })
}

async function openReader(harness: Harness, sourceId: string): Promise<void> {
  await harness.window.evaluate((id) => {
    window.location.hash = `#/reader/${id}`
  }, sourceId)
}

test('video entry mounts the floating player and syncs both ways', async ({ harness }) => {
  await expectEngineState(harness.window, 'ready')
  await seedReader(harness, VIDEO_ID, 'video')
  await openReader(harness, VIDEO_ID)

  // The player mounts beside the transcript with the video skin.
  const panel = harness.window.locator('.media-player')
  await expect(panel).toBeVisible()
  await expect(panel).toHaveAttribute('data-kind', 'video')
  await expect(panel.locator('video.media-video')).toHaveCount(1)

  const frame = harness.window.frameLocator('iframe.reader-frame')
  await expect(frame.locator('p.passage')).toHaveCount(3)

  // iframe → parent: clicking the third passage seeks the <video> to 10s.
  await frame.locator('p.passage').nth(2).click()
  await expect
    .poll(async () =>
      harness.window.locator('video.media-video').evaluate((v: HTMLVideoElement) => v.currentTime)
    )
    .toBe(10)

  // parent → iframe: a player time update highlights the matching passage.
  // Drive a timeupdate deterministically (placeholder bytes don't decode).
  await harness.window.locator('video.media-video').evaluate((v: HTMLVideoElement) => {
    v.currentTime = 6
    v.dispatchEvent(new Event('timeupdate'))
  })
  await expect(frame.locator('p.passage.sync-active')).toHaveText('second passage')
})

test('audio entry gets the compact audio skin and still syncs', async ({ harness }) => {
  await expectEngineState(harness.window, 'ready')
  await seedReader(harness, AUDIO_ID, 'audio')
  await openReader(harness, AUDIO_ID)

  const panel = harness.window.locator('.media-player')
  await expect(panel).toBeVisible()
  await expect(panel).toHaveAttribute('data-kind', 'audio')
  await expect(panel.locator('audio.media-audio')).toHaveCount(1)
  await expect(panel.locator('video')).toHaveCount(0)

  const frame = harness.window.frameLocator('iframe.reader-frame')
  await frame.locator('p.passage').nth(1).click()
  await expect
    .poll(async () =>
      harness.window.locator('audio.media-audio').evaluate((a: HTMLAudioElement) => a.currentTime)
    )
    .toBe(5)
})

test('youtube entry mounts the embed iframe and falls back to a link on embed error', async ({
  harness
}) => {
  await expectEngineState(harness.window, 'ready')
  const id = createHash('sha256').update('youtube-entry').digest('hex')
  await seedReader(harness, id, 'youtube')
  await openReader(harness, id)

  const panel = harness.window.locator('.media-player')
  await expect(panel).toBeVisible()
  await expect(panel).toHaveAttribute('data-kind', 'youtube')
  // The iframe loads the engine-hosted embed page (loopback http origin). The
  // mock embed page posts an `error`, so the app reveals the "Watch on
  // YouTube" link (opens the OS browser) instead of a dead black box.
  const fallback = panel.locator('a.media-youtube-fallback')
  await expect(fallback).toBeVisible()
  await expect(fallback).toHaveAttribute('href', /youtube\.com\/watch\?v=/)
  await expect(panel.locator('iframe.media-youtube')).toBeHidden()
})

test('unavailable media leaves the Reader transcript-only', async ({ harness }) => {
  await expectEngineState(harness.window, 'ready')
  const id = createHash('sha256').update('unavailable-entry').digest('hex')
  await seedReader(harness, id, 'unavailable')
  await openReader(harness, id)

  await expect(harness.window.locator('iframe.reader-frame')).toBeVisible()
  await expect(harness.window.locator('.media-player')).toHaveCount(0)
})
