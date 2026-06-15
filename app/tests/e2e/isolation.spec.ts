import { expect, expectEngineState, test } from './fixtures'

/**
 * Renderer isolation e2e (app-shell spec: credential-free renderer; app-views
 * spec: Reader artifact isolation; tasks 3.3 + 4.3's deferred assertions).
 */

/**
 * A stand-in for the html.py artifact: self-contained HTML whose inline
 * script mirrors the chapter scroll-sync behavior (TOC click → scroll) and
 * additionally probes its own confinement.
 */
const ARTIFACT_HTML = `<!DOCTYPE html>
<html>
  <head><style>body { font-family: sans-serif; } section { min-height: 120vh; }</style></head>
  <body>
    <nav><a id="toc-ch2" href="#ch2">Chapter 2</a></nav>
    <section id="ch1"><h2>Chapter 1</h2><p>hello transcript</p></section>
    <section id="ch2"><h2>Chapter 2</h2><p>second chapter</p></section>
    <div id="probe"></div>
    <script>
      const probe = document.getElementById('probe')
      probe.dataset.scriptRan = 'yes'
      probe.dataset.origin = String(self.origin)
      probe.dataset.bridge = typeof window.api
      try {
        void window.parent.document
        probe.dataset.parentAccess = 'reachable'
      } catch {
        probe.dataset.parentAccess = 'blocked'
      }
      document.getElementById('toc-ch2').addEventListener('click', (event) => {
        event.preventDefault()
        document.getElementById('ch2').scrollIntoView()
        probe.dataset.scrolled = 'yes'
      })
    </script>
  </body>
</html>`

test('engine bearer token is unreachable from the renderer', async ({ harness }) => {
  await expectEngineState(harness.window, 'ready')
  const probe = await harness.window.evaluate(() => {
    const api = (window as unknown as Record<string, unknown>)['api']
    return {
      hasRequire: 'require' in window,
      processType: typeof (window as unknown as Record<string, unknown>)['process'],
      apiType: typeof api,
      apiKeys: api === undefined ? [] : Object.keys(api as object).sort(),
      // Everything string-reachable from the renderer surface: a leaked token
      // would have to live somewhere serializable.
      haystack: JSON.stringify([
        document.documentElement.outerHTML,
        JSON.stringify(window.localStorage),
        JSON.stringify(window.sessionStorage),
        String(window.name)
      ])
    }
  })
  expect(probe.hasRequire).toBe(false)
  expect(probe.processType).toBe('undefined') // sandboxed renderer: no node globals
  expect(probe.apiType).toBe('object')
  // The bridge is the renderer's ONLY door, and it is payload-only.
  expect(probe.apiKeys).toEqual(
    [
      'getEngineStatus',
      'submitJob',
      'listJobs',
      'getJob',
      'confirmJob',
      'dismissJob',
      'listLibrary',
      'transcriptHtml',
      'mediaInfo',
      'youtubeEmbedUrl',
      'getSettings',
      'putSettings',
      'putKey',
      'testKey',
      'keyStorageMode',
      'listProviders',
      'listPacks',
      'installPack',
      'uninstallPack',
      'isFirstRunComplete',
      'markFirstRunComplete',
      'startPairing',
      'listCookieJars',
      'deleteCookieJar',
      'getPathForFile',
      'getUpdateStatus',
      'installUpdate',
      'engineRestart',
      'onEngineStatus',
      'onPipelineEvent',
      'onJobsHydrated',
      'onProtocolRequest',
      'onUpdateStatus'
    ].sort()
  )
  expect(probe.haystack).not.toContain(harness.mock.token)
})

test('Reader renders the artifact in an opaque-origin sandbox with its script working', async ({
  harness
}) => {
  await expectEngineState(harness.window, 'ready')
  await harness.mock.control('/seed', {
    library: [
      {
        source_id: 'ep-isolated',
        source: 'https://example.com/episode',
        title: 'Isolated Episode',
        html_path: '/mock/ep-isolated.html',
        created_at: Date.now() / 1000
      }
    ],
    transcripts: { 'ep-isolated': ARTIFACT_HTML }
  })
  await harness.window.evaluate(() => {
    window.location.hash = '#/reader/ep-isolated'
  })

  const frame = harness.window.frameLocator('iframe.reader-frame')
  const probe = frame.locator('#probe')
  // The artifact's inline script executed (srcdoc inherits the page CSP,
  // which deliberately allows inline scripts for exactly this).
  await expect(probe).toHaveAttribute('data-script-ran', 'yes')
  // sandbox="allow-scripts" WITHOUT allow-same-origin: opaque origin.
  await expect(probe).toHaveAttribute('data-origin', 'null')
  // No preload bridge, no parent access — no path to IPC or the token.
  await expect(probe).toHaveAttribute('data-bridge', 'undefined')
  await expect(probe).toHaveAttribute('data-parent-access', 'blocked')

  // The chapter scroll behavior functions inside the sandbox.
  await frame.locator('#toc-ch2').click()
  await expect(probe).toHaveAttribute('data-scrolled', 'yes')
  const scrolled = await frame.locator('body').evaluate(() => window.scrollY)
  expect(scrolled).toBeGreaterThan(0)

  // The iframe carries exactly the sandbox the design prescribes.
  await expect(harness.window.locator('iframe.reader-frame')).toHaveAttribute(
    'sandbox',
    'allow-scripts'
  )
})
