import { chromium, expect, test } from '@playwright/test'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

/**
 * Anchor-offset regression gate for the transcript artifact (issue #63).
 *
 * A fixed scroll-padding cannot track the variable-height sticky jump rail
 * (#50 shipped the defect at 1.5rem, #60's wrap re-introduced it at 4rem).
 * The artifact now measures the rail and sets scroll-padding itself; this
 * spec loads the committed longform golden (kept current by pytest's
 * byte-compare — see tests/regen_goldens.py at the repo root) in a plain
 * chromium page and asserts, at both walk widths:
 *
 *   1. effective scroll-padding-top >= the rail's live height, and
 *   2. after an anchor jump settles, the target's top edge sits at or
 *      below the rail's bottom edge — never under it.
 *
 * No Electron, no engine: this is renderer-output geometry only.
 */

const longformArtifactUrl = pathToFileURL(
  path.resolve(__dirname, '../../../tests/fixtures/sample_expected_longform.html')
).href
const nearHourArtifactUrl = pathToFileURL(
  path.resolve(__dirname, '../../../tests/fixtures/sample_expected_near_hour.html')
).href
const chapteredArtifactUrl = pathToFileURL(
  path.resolve(__dirname, '../../../tests/fixtures/sample_expected.html')
).href
const searchArtifactUrl = pathToFileURL(
  path.resolve(__dirname, '../../../tests/fixtures/sample_expected_search.html')
).href

test('search tolerates extension decoration but rejects transcript mutation (#92)', async () => {
  const browser = await chromium.launch()
  try {
    const source = await readFile(
      path.resolve(__dirname, '../../../tests/fixtures/sample_expected_search.html'),
      'utf8'
    )
    const page = await browser.newPage()
    const mutations = [
      'password-manager-input-attribute',
      'grammarly-body-attribute',
      'dark-reader-html-style',
      'dark-reader-head-style',
      'body-overlay'
    ] as const

    for (const mutation of mutations) {
      await page.setContent(source)
      await page.keyboard.press('/')
      const input = page.getByRole('searchbox', { name: 'Find in transcript' })
      await input.fill('resilience')
      await expect(page.getByRole('status')).toContainText('1 of 2')
      await page.evaluate((kind) => {
        if (kind === 'password-manager-input-attribute') {
          document.querySelector('.transcript-search-input')
            ?.setAttribute('data-lastpass-icon-root', 'true')
        } else if (kind === 'grammarly-body-attribute') {
          document.body.setAttribute('data-gr-ext-installed', '')
        } else if (kind === 'dark-reader-html-style') {
          document.documentElement.style.setProperty('background-color', '#181a1b')
        } else if (kind === 'dark-reader-head-style') {
          const style = document.createElement('style')
          style.textContent = 'body{}'
          document.head.appendChild(style)
        } else {
          document.body.appendChild(document.createElement('div'))
        }
      }, mutation)
      await input.fill('회복')
      await expect(page.getByRole('status')).toContainText('1 of 1')
    }

    await page.setContent(source)
    await page.evaluate(() => {
      document.documentElement.style.setProperty('color-scheme', 'dark')
      document.body.setAttribute('data-gr-ext-installed', '')
    })
    await page.keyboard.press('/')
    await page.getByRole('searchbox', { name: 'Find in transcript' }).fill('회복')
    await expect(page.getByRole('status')).toContainText('1 of 1')

    await page.evaluate(() => {
      document.querySelector('p[data-start]')?.append(' genuinely changed')
    })
    await expect(page.getByRole('status')).toHaveText('Transcript changed; reopen it to search.')
  } finally {
    await browser.close()
  }
})

test('search rejects moving or noncanonical transcript DOM and preserves cross-node NFKC (#88)', async () => {
  const browser = await chromium.launch()
  try {
    const source = await readFile(
      path.resolve(__dirname, '../../../tests/fixtures/sample_expected_search.html'),
      'utf8'
    )
    const page = await browser.newPage()
    const loadDocument = async (document: string) => {
      await page.goto('about:blank')
      await page.setContent(document)
    }
    const expectRejected = async (document: string, message = 'This transcript cannot be searched.') => {
      await loadDocument(document)
      await page.keyboard.press('/')
      await expect(page.getByRole('status')).toHaveText(message)
      await page.getByRole('button', { name: 'Clear and close search' }).click()
      await page.keyboard.press('/')
      await expect(page.getByRole('status')).toHaveText(message)
    }
    await loadDocument(
      source.replace(
        'Resilience begins with deliberate practice.',
        'Cafe<strong>́</strong> begins with deliberate practice.'
      )
    )
    await page.keyboard.press('/')
    const input = page.getByRole('searchbox', { name: 'Find in transcript' })
    await input.fill('Café')
    await expect(page.getByRole('status')).toContainText('1 of 1')

    await page.evaluate(() => {
      document.querySelector('p[data-start]')?.append(' changed')
    })
    await expect(page.getByRole('status')).toHaveText('Transcript changed; reopen it to search.')

    await loadDocument(source)
    await page.keyboard.press('/')
    await page.getByRole('searchbox', { name: 'Find in transcript' }).fill('resilience')
    await expect(page.getByRole('status')).toContainText('1 of 2')
    await page.evaluate(() => document.querySelector('main')?.setAttribute('data-hostile', 'true'))
    await expect(page.locator('.transcript-search-status')).toHaveText(
      'Transcript changed; reopen it to search.'
    )

    await loadDocument(source)
    await page.keyboard.press('/')
    await page.getByRole('searchbox', { name: 'Find in transcript' }).fill('resilience')
    await expect(page.getByRole('status')).toContainText('1 of 2')
    await page.evaluate(() => {
      const main = document.querySelector('#content > main')
      const wrapper = document.createElement('section')
      main?.parentElement?.append(wrapper)
      if (main !== null) wrapper.append(main)
    })
    await expect(page.getByRole('status')).toHaveText('Transcript changed; reopen it to search.')

    const forged = source.replace(
      '<main>',
      '<p data-start="90" data-end="91">forged header passage</p><main>'
    )
    await loadDocument(forged)
    await page.keyboard.press('/')
    await page.getByRole('searchbox', { name: 'Find in transcript' }).fill('forged header')
    await expect(page.getByRole('status')).toHaveText('No matches.')

    const oversized = source.replace(
      'Resilience begins with deliberate practice.',
      'x'.repeat(100_001)
    )
    await loadDocument(oversized)
    await page.keyboard.press('/')
    await expect(page.getByRole('status')).toHaveText('This transcript is too large to search.')
    await page.getByRole('button', { name: 'Clear and close search' }).click()
    await page.keyboard.press('/')
    await expect(page.getByRole('status')).toHaveText('This transcript is too large to search.')

    const excludedNodes = source.replace(
      '<main>',
      `<aside>${'<i></i>'.repeat(100_001)}</aside><main>`
    )
    await loadDocument(excludedNodes)
    await page.keyboard.press('/')
    await page.getByRole('searchbox', { name: 'Find in transcript' }).fill('resilience')
    await expect(page.getByRole('status')).toContainText('1 of 2')

    const expanding = 'ﷺ'.repeat(100_000)
    const normalizedOversized = source
      .replace('Resilience begins with deliberate practice.', expanding)
      .replace('회복탄력성은 다시 시작하는 힘입니다.', expanding)
      .replace('RESILIENCE returns in the closing evidence.', expanding)
    await loadDocument(normalizedOversized)
    await page.keyboard.press('/')
    await expect(page.getByRole('status')).toHaveText('This transcript is too large to search.')

    await expectRejected(
      source.replace(
        'Resilience begins with deliberate practice.',
        'Resilience begins <span hidden>hidden canary</span> with deliberate practice.'
      )
    )
    await expectRejected(
      source.replace(
        'Resilience begins with deliberate practice.',
        'Resilience begins <script>void 0</script> with deliberate practice.'
      )
    )
    await expectRejected(
      source.replace(
        'Resilience begins with deliberate practice.',
        'Resilience begins <em>unexpected</em> with deliberate practice.'
      )
    )
    await expectRejected(source.replace(
      '<main>',
      '<main><template><p data-start="1" data-end="2">hidden</p></template>'
    ))
    await expectRejected(source.replace('id="t-0"', 'id="t-0" class="search-match-active"'))
    await expectRejected(source.replace('autocomplete="off"', 'name="query" autocomplete="on"'))
    await expectRejected(source.replace(' spellcheck="false"', ''))
    await expectRejected(source.replace(
      '<span class="ts">00:00:00</span>',
      '<span class="ts">00:00:00</span><span class="ts">00:00:01</span>'
    ))

    await loadDocument(
      source.replace('Resilience begins with deliberate practice.', 'Resilience\n\t begins with deliberate practice.')
    )
    await page.keyboard.press('/')
    await page.getByRole('searchbox', { name: 'Find in transcript' }).fill('resilience begins')
    await expect(page.getByRole('status')).toContainText('1 of 1')

    await loadDocument(source)
    await page.keyboard.press('/')
    await page.evaluate(() => {
      document.querySelector('p[data-start]')?.append(' same-task-hostile')
      const input = document.querySelector<HTMLInputElement>('.transcript-search-input')
      if (input === null) throw new Error('search input missing')
      input.value = 'resilience'
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })
    await expect(page.getByRole('status')).toHaveText('Transcript changed; reopen it to search.')

    await loadDocument(source)
    await page.keyboard.press('/')
    const activeSearch = page.getByRole('searchbox', { name: 'Find in transcript' })
    await activeSearch.fill('resilience')
    await expect(page.locator('p.search-match')).toHaveCount(2)
    await page.evaluate(() => {
      document.querySelector('p[data-start]')?.append(' resize-hostile')
      dispatchEvent(new Event('resize'))
    })
    await expect(page.getByRole('status')).toHaveText('Transcript changed; reopen it to search.')
    await expect(page.locator('p.search-match')).toHaveCount(0)

    for (const mutation of ['expanded', 'hidden', 'disabled'] as const) {
      await loadDocument(source)
      await page.evaluate((kind) => {
        const input = document.querySelector('.transcript-search-input')
        input?.addEventListener('focus', () => {
          if (kind === 'expanded') {
            document.querySelector('.transcript-search-toggle')?.setAttribute('aria-expanded', 'false')
          } else if (kind === 'hidden') {
            const panel = document.querySelector<HTMLElement>('.transcript-search-panel')
            if (panel) panel.hidden = true
          } else {
            const previous = document.querySelector<HTMLButtonElement>('.transcript-search-prev')
            if (previous) previous.disabled = false
          }
        }, { once: true })
      }, mutation)
      await page.keyboard.press('/')
      await expect(page.getByRole('status')).toHaveText('Transcript changed; reopen it to search.')
      await expect(page.locator('p.search-match')).toHaveCount(0)
    }
    await expect(page.getByRole('button', { name: 'Next match' })).toBeDisabled()
    await expect(page.locator('body')).not.toHaveClass(/transcript-search-active/)

    await loadDocument(source)
    await page.evaluate(() => {
      const input = document.querySelector('.transcript-search-input')
      input?.addEventListener('focus', () => {
        document.querySelector('p[data-start]')?.append(' focus-hostile')
      }, { once: true })
    })
    await page.getByRole('button', { name: /Find in transcript/ }).click()
    await expect(page.getByRole('status')).toHaveText('Transcript changed; reopen it to search.')
    await expect(page.locator('p.search-match')).toHaveCount(0)

    await loadDocument(source)
    await page.keyboard.press('/')
    await page.evaluate(() => {
      document.querySelector('p[data-start]')?.classList.add('search-match-active')
    })
    await expect(page.getByRole('status')).toHaveText('Transcript changed; reopen it to search.')

    await loadDocument(source)
    await page.keyboard.press('/')
    await page.evaluate(() => {
      document.querySelector('.transcript-search-input')?.setAttribute('name', 'private-canary')
    })
    await expect(page.getByRole('status')).toHaveText('Transcript changed; reopen it to search.')
  } finally {
    await browser.close()
  }
})

test('search honors reduced motion and chaptered sidebar geometry (#88)', async ({}, testInfo) => {
  const browser = await chromium.launch()
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } })
    await page.emulateMedia({ reducedMotion: 'reduce', colorScheme: 'dark' })
    await page.goto(chapteredArtifactUrl)
    await page.keyboard.press('/')
    const input = page.getByRole('searchbox', { name: 'Find in transcript' })
    await input.fill('show')
    await expect(page.getByRole('status')).toContainText('1 of 2')
    const geometry = await page.evaluate(() => {
      const search = document.querySelector<HTMLElement>('.transcript-search')
      const main = document.querySelector<HTMLElement>('#content > main')
      const sidebar = document.querySelector<HTMLElement>('#sidebar')
      if (search === null || main === null || sidebar === null) throw new Error('chapter geometry missing')
      return {
        scrollBehavior: getComputedStyle(document.documentElement).scrollBehavior,
        searchLeft: search.getBoundingClientRect().left,
        mainLeft: main.getBoundingClientRect().left,
        sidebarWidth: sidebar.getBoundingClientRect().width,
        overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth
      }
    })
    expect(geometry.scrollBehavior).toBe('auto')
    expect(Math.abs(geometry.searchLeft - geometry.mainLeft)).toBeLessThanOrEqual(1)
    expect(geometry.sidebarWidth).toBeCloseTo(280, 0)
    expect(geometry.overflow).toBeLessThanOrEqual(1)
    await input.press('Enter')
    await expect(page.getByRole('status')).toContainText('2 of 2')
    const screenshot = testInfo.outputPath('transcript-search-1280-dark-chaptered.png')
    await page.screenshot({ path: screenshot, fullPage: true, caret: 'initial' })
    await testInfo.attach('transcript-search-1280-dark-chaptered.png', {
      path: screenshot,
      contentType: 'image/png'
    })
  } finally {
    await browser.close()
  }
})

test('search and media sync coexist in the opaque-style iframe boundary (#88)', async ({}, testInfo) => {
  const browser = await chromium.launch()
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } })
    await page.emulateMedia({ reducedMotion: 'reduce', colorScheme: 'dark' })
    const source = await readFile(
      path.resolve(__dirname, '../../../tests/fixtures/sample_expected_search.html'),
      'utf8'
    )
    await page.setContent(
      '<style>html,body{margin:0}iframe{width:100%;height:600px;border:0}</style>' +
      '<iframe sandbox="allow-scripts"></iframe>'
    )
    await page.locator('iframe').evaluate((node, documentSource) => {
      ;(node as HTMLIFrameElement).srcdoc = documentSource
    }, source)
    const frame = page.frameLocator('iframe')
    await expect(frame.getByText('Resilience begins with deliberate practice.')).toBeVisible()
    const postTime = async (time: number) => page.locator('iframe').evaluate((node, value) => {
      ;(node as HTMLIFrameElement).contentWindow?.postMessage({ ch: 'pr-sync', type: 'time', t: value }, '*')
    }, time)

    await test.step('initial media sync', async () => {
      await postTime(0)
      await expect(frame.locator('p.sync-active')).toContainText('Resilience begins', { timeout: 3000 })
    })
    await frame.getByRole('button', { name: /Find in transcript/ }).click()
    const input = frame.getByRole('searchbox', { name: 'Find in transcript' })
    await input.fill('resilience')
    await expect(frame.getByRole('status')).toContainText('1 of 2')
    await test.step('combined search and media state', async () => {
      const before = await frame.locator('html').evaluate(() => scrollY)
      await postTime(40)
      await expect(frame.locator('p.sync-active')).toContainText('RESILIENCE returns', { timeout: 3000 })
      await expect(frame.locator('p.search-match-active')).toContainText('Resilience begins')
      const frames = await frame.locator('html').evaluate(async () => {
        const immediate = scrollY
        await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()))
        const first = scrollY
        await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()))
        return { immediate, first, second: scrollY }
      })
      expect(frames).toEqual({ immediate: before, first: before, second: before })
    })
    const combined = await frame.locator('p.search-match.sync-active').evaluate((node) => ({
        classes: node.className,
        transition: getComputedStyle(node).transitionDuration,
        boxShadow: getComputedStyle(node).boxShadow
      }), undefined, { timeout: 3000 })
    expect(combined.classes).toContain('search-match')
    expect(combined.transition).toBe('0s')
    expect(combined.boxShadow).not.toBe('none')
    const screenshot = testInfo.outputPath('transcript-search-390-dark-sync-combined.png')
    await page.screenshot({ path: screenshot, fullPage: true, caret: 'initial' })
    await testInfo.attach('transcript-search-390-dark-sync-combined.png', {
      path: screenshot,
      contentType: 'image/png'
    })

    await test.step('dismiss restores media-only state', async () => {
      await frame.locator('.transcript-search-clear').evaluate((node) => {
        ;(node as HTMLButtonElement).click()
      })
      await expect(frame.locator('p.search-match')).toHaveCount(0)
      const before = await frame.locator('html').evaluate(() => {
        scrollTo(0, document.body.scrollHeight)
        return scrollY
      })
      await postTime(0)
      await expect(frame.locator('p.sync-active')).toContainText('Resilience begins', { timeout: 3000 })
      const frames = await frame.locator('html').evaluate(async () => {
        const immediate = scrollY
        await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()))
        const first = scrollY
        await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()))
        return { immediate, first, second: scrollY }
      })
      expect(frames.immediate).not.toBe(before)
      expect(frames.first).toBe(frames.immediate)
      expect(frames.second).toBe(frames.immediate)
    })
  } finally {
    await browser.close()
  }
})

test('in-transcript search is multilingual, keyboard-complete, private, and resettable (#88)', async () => {
  const browser = await chromium.launch()
  try {
    const page = await browser.newPage({ viewport: { width: 390, height: 844 } })
    const consoleMessages: string[] = []
    const requests: Array<{ url: string; method: string; body: string; headers: Record<string, string> }> = []
    page.on('console', (message) => consoleMessages.push(message.text()))
    page.on('request', (request) =>
      requests.push({
        url: request.url(),
        method: request.method(),
        body: request.postData() ?? '',
        headers: request.headers()
      })
    )
    await page.goto(searchArtifactUrl)

    const opener = page.locator('.transcript-search-toggle')
    await expect(opener).toHaveAttribute('aria-expanded', 'false')
    await page.keyboard.press('/')
    const input = page.getByRole('searchbox', { name: 'Find in transcript' })
    await expect(input).toBeFocused()
    await expect(opener).toHaveAttribute('aria-expanded', 'true')
    await input.press('Escape')
    await expect(opener).toBeFocused()
    await page.keyboard.press('/')
    await expect(page.getByRole('button', { name: 'Previous match' })).toBeDisabled()
    await expect(page.getByRole('button', { name: 'Next match' })).toBeDisabled()
    await expect(page.getByRole('button', { name: 'Read current passage' })).toBeDisabled()

    await input.fill('resilience')
    await input.fill('회복탄력성')
    await expect(page.getByRole('status')).toContainText('1 of 1')
    await expect(page.locator('p.search-match-active')).toContainText('회복탄력성')
    await input.fill('resilience')
    await expect(page.getByRole('status')).toContainText('1 of 2')
    await expect(page.locator('p.search-match')).toHaveCount(2)
    await expect(page.locator('p.search-match-active')).toContainText('Resilience begins')
    await expect(page.getByRole('button', { name: 'Previous match' })).toBeEnabled()
    await expect(page.getByRole('button', { name: 'Next match' })).toBeEnabled()
    await expect(page.getByRole('button', { name: 'Read current passage' })).toBeEnabled()
    await input.press('Enter')
    await expect(page.getByRole('status')).toContainText('2 of 2')
    await expect(page.locator('p.search-match-active')).toContainText('RESILIENCE returns')
    await input.press('Shift+Enter')
    await expect(page.getByRole('status')).toContainText('1 of 2')
    await page.getByRole('button', { name: 'Read current passage' }).click()
    await expect(page.locator('p[aria-current="location"]')).toBeFocused()
    await page.keyboard.press('Escape')
    await expect(opener).toBeFocused()
    await expect(opener).toHaveAttribute('aria-expanded', 'false')

    await page.keyboard.press('/')
    await input.fill('resilience')
    await page.getByRole('button', { name: 'Next match' }).focus()
    await page.keyboard.press('Escape')
    await expect(opener).toBeFocused()
    await page.keyboard.press('/')
    await input.fill('회복탄력성')
    await expect(page.getByRole('status')).toContainText('1 of 1')
    await expect(page.locator('p.search-match-active')).toContainText('회복탄력성')

    await input.fill('😀'.repeat(101))
    await expect(page.getByRole('status')).toHaveText('Search is limited to 100 characters.')
    await expect(page.locator('p.search-match')).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Previous match' })).toBeDisabled()
    await expect(page.getByRole('button', { name: 'Next match' })).toBeDisabled()
    await expect(page.getByRole('button', { name: 'Read current passage' })).toBeDisabled()

    await input.fill(`${' '.repeat(100)}resilience`)
    await expect(page.getByRole('status')).toContainText('1 of 2')

    const dispatchMilliseconds = await page.evaluate(() => {
      const target = document.querySelector<HTMLInputElement>('.transcript-search-input')
      if (target === null) throw new Error('search input missing')
      target.value = 'x'.repeat(2_000_000)
      const started = performance.now()
      target.dispatchEvent(new Event('input', { bubbles: true }))
      return performance.now() - started
    })
    expect(dispatchMilliseconds).toBeLessThan(250)
    await expect(page.getByRole('status')).toHaveText('Search is limited to 100 characters.')

    await input.dispatchEvent('compositionstart')
    await input.fill('resilience')
    await input.dispatchEvent('keydown', { key: 'Enter', isComposing: true })
    await page.waitForTimeout(200)
    await expect(page.locator('p.search-match')).toHaveCount(0)
    await input.dispatchEvent('compositionend')
    await expect(page.getByRole('status')).toContainText('1 of 2')

    const canary = 'k4-search-query-81f5c2'
    await input.fill(canary)
    await expect(page.getByRole('status')).toHaveText('No matches.')
    await expect(page.getByRole('button', { name: 'Previous match' })).toBeDisabled()
    await expect(page.getByRole('button', { name: 'Next match' })).toBeDisabled()
    await expect(page.getByRole('button', { name: 'Read current passage' })).toBeDisabled()
    const privacySweep = async (includeInputs: boolean) => page.evaluate(async (scanInputs) => {
      const safely = async (read: () => unknown | Promise<unknown>): Promise<string> => {
        try {
          return JSON.stringify(await read())
        } catch (error) {
          if (error instanceof DOMException && error.name === 'SecurityError') return ''
          throw error
        }
      }
      const databaseContents = async () => {
        const snapshots: unknown[] = []
        for (const info of await indexedDB.databases()) {
          if (!info.name) continue
          const database = await new Promise<IDBDatabase>((resolve, reject) => {
            const request = indexedDB.open(info.name as string)
            request.onsuccess = () => resolve(request.result)
            request.onerror = () => reject(request.error)
          })
          for (const storeName of Array.from(database.objectStoreNames)) {
            const store = database.transaction(storeName, 'readonly').objectStore(storeName)
            const read = (request: IDBRequest) => new Promise((resolve, reject) => {
              request.onsuccess = () => resolve(request.result)
              request.onerror = () => reject(request.error)
            })
            const keysRequest = store.getAllKeys()
            const valuesRequest = store.getAll()
            snapshots.push({ database: info.name, store: storeName,
              keys: await read(keysRequest), values: await read(valuesRequest) })
          }
          database.close()
        }
        return snapshots
      }
      const cacheContents = async () => {
        const snapshots: unknown[] = []
        for (const name of await caches.keys()) {
          const cache = await caches.open(name)
          for (const request of await cache.keys()) {
            const response = await cache.match(request)
            snapshots.push({
              name,
              url: request.url,
              requestHeaders: Array.from(request.headers.entries()),
              requestBody: await request.clone().text(),
              responseHeaders: response ? Array.from(response.headers.entries()) : [],
              body: response ? await response.clone().text() : ''
            })
          }
        }
        return snapshots
      }
      return {
        url: location.href,
        attributes: Array.from(document.querySelectorAll('*')).flatMap((element) =>
          element.getAttributeNames().map((name) => `${name}=${element.getAttribute(name) ?? ''}`)
        ),
        local: await safely(() => JSON.stringify(localStorage)),
        session: await safely(() => JSON.stringify(sessionStorage)),
        databases: await safely(databaseContents),
        caches: await safely(cacheContents),
        cookie: await safely(() => document.cookie),
        inputs: scanInputs
          ? Array.from(document.querySelectorAll<HTMLInputElement>('input')).map((item) => item.value)
          : []
      }
    }, includeInputs)
    const privacy = await privacySweep(false)
    expect(JSON.stringify(privacy)).not.toContain(canary)
    expect(consoleMessages.join('\n')).not.toContain(canary)
    expect(JSON.stringify(requests)).not.toContain(canary)

    await page.goto('about:blank')
    await page.goBack()
    await expect(page.locator('.transcript-search-toggle')).toHaveAttribute('aria-expanded', 'false')
    await expect(page.getByRole('searchbox', { name: 'Find in transcript' })).toBeHidden()
    expect(await page.locator('.transcript-search-input').inputValue()).toBe('')
    expect(JSON.stringify(await privacySweep(true))).not.toContain(canary)

    await page.keyboard.press('/')
    await page.getByRole('searchbox', { name: 'Find in transcript' }).fill('resilience')
    await expect(page.getByRole('status')).toContainText('1 of 2')
    await page.getByRole('button', { name: 'Clear and close search' }).click()
    await expect(opener).toBeFocused()
    await expect(opener).toHaveAttribute('aria-expanded', 'false')
    await expect(page.locator('p.search-match')).toHaveCount(0)
    expect(JSON.stringify(await privacySweep(true))).not.toContain(canary)
    await page.keyboard.press('Control+/')
    await expect(opener).toBeVisible()
    await page.reload()
    await expect(page.getByRole('button', { name: /Find in transcript/ })).toHaveAttribute(
      'aria-expanded',
      'false'
    )
    await expect(page.getByRole('searchbox', { name: 'Find in transcript' })).toBeHidden()
  } finally {
    await browser.close()
  }
})

test('computed search, active, sync, and combined styles use the reviewed theme tokens (#88)', async () => {
  const browser = await chromium.launch()
  try {
    for (const theme of ['dark', 'light'] as const) {
      const page = await browser.newPage()
      await page.emulateMedia({ reducedMotion: 'reduce' })
      await page.goto(searchArtifactUrl)
      await page.evaluate((selectedTheme) => { document.documentElement.dataset.theme = selectedTheme }, theme)
      await page.keyboard.press('/')
      await page.getByRole('searchbox', { name: 'Find in transcript' }).fill('resilience')
      await expect(page.getByRole('status')).toContainText('1 of 2')
      const styles = await page.evaluate(() => {
        const passages = Array.from(document.querySelectorAll<HTMLElement>('p[data-start]'))
        const active = document.querySelector<HTMLElement>('p.search-match-active')
        const passive = document.querySelector<HTMLElement>('p.search-match:not(.search-match-active)')
        const syncOnly = passages.find((passage) => !passage.classList.contains('search-match'))
        if (!active || !passive || !syncOnly) throw new Error('style states missing')
        const read = (node: HTMLElement) => {
          const style = getComputedStyle(node)
          return {
            background: style.backgroundColor,
            color: style.color,
            borderColor: style.borderLeftColor,
            borderStyle: style.borderLeftStyle,
            outline: style.outlineColor,
            shadow: style.boxShadow
          }
        }
        const rootStyle = getComputedStyle(document.documentElement)
        const result = { passive: read(passive), active: read(active), syncOnly: {}, combined: {},
          activeSync: {}, background: getComputedStyle(document.body).backgroundColor,
          link: rootStyle.getPropertyValue('--link').trim() }
        syncOnly.classList.add('sync-active')
        result.syncOnly = read(syncOnly)
        syncOnly.classList.remove('sync-active')
        passive.classList.add('sync-active')
        result.combined = read(passive)
        passive.classList.remove('sync-active')
        active.classList.add('sync-active')
        result.activeSync = read(active)
        return result
      })
      const expected = theme === 'dark'
        ? { edge: 'rgb(154, 117, 53)', accent: 'rgb(212, 160, 74)', link: 'rgb(91, 164, 207)' }
        : { edge: 'rgb(164, 91, 74)', accent: 'rgb(154, 59, 46)', link: 'rgb(42, 111, 151)' }
      const parse = (value: string): [number, number, number, number] => {
        if (/^#[0-9a-f]{6}$/i.test(value)) {
          return [
            Number.parseInt(value.slice(1, 3), 16),
            Number.parseInt(value.slice(3, 5), 16),
            Number.parseInt(value.slice(5, 7), 16),
            1
          ]
        }
        const channels = value.match(/[\d.]+/g)?.map(Number) ?? []
        if (channels.length < 3) throw new Error(`unparseable color ${value}`)
        return [channels[0] ?? 0, channels[1] ?? 0, channels[2] ?? 0, channels[3] ?? 1]
      }
      const composite = (foreground: string, background: string): string => {
        const [fr, fg, fb, alpha] = parse(foreground)
        const [br, bg, bb] = parse(background)
        return `rgb(${fr * alpha + br * (1 - alpha)}, ${fg * alpha + bg * (1 - alpha)}, ${fb * alpha + bb * (1 - alpha)})`
      }
      const luminance = (value: string): number => {
        const channels = parse(value).slice(0, 3).map((channel) => {
          const normalized = channel / 255
          return normalized <= 0.04045 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4
        })
        return 0.2126 * (channels[0] ?? 0) + 0.7152 * (channels[1] ?? 0) + 0.0722 * (channels[2] ?? 0)
      }
      const contrast = (first: string, second: string): number => {
        const [lighter, darker] = [luminance(first), luminance(second)].sort((a, b) => b - a)
        return ((lighter ?? 0) + 0.05) / ((darker ?? 0) + 0.05)
      }
      const passiveBackground = composite(styles.passive.background, styles.background)
      expect(styles.passive.borderStyle).toBe('dashed')
      expect(styles.passive.borderColor).toBe(expected.edge)
      expect(contrast(styles.passive.color, passiveBackground)).toBeGreaterThanOrEqual(4.5)
      expect(contrast(styles.passive.borderColor, passiveBackground)).toBeGreaterThanOrEqual(3)
      expect(styles.active.outline).toBe(expected.accent)
      expect(contrast(styles.active.outline, styles.background)).toBeGreaterThanOrEqual(3)
      expect((styles.syncOnly as { shadow: string }).shadow).toContain(expected.link)
      expect((styles.combined as { shadow: string }).shadow).toContain(expected.link)
      expect((styles.combined as { borderStyle: string }).borderStyle).toBe('dashed')
      expect((styles.activeSync as { shadow: string }).shadow).toContain(expected.link)
      expect((styles.activeSync as { borderColor: string }).borderColor).toBe(expected.accent)
      expect(contrast(styles.link, styles.background)).toBeGreaterThanOrEqual(3)
      await page.close()
    }
  } finally {
    await browser.close()
  }
})

for (const viewport of [
  { width: 390, height: 844 },
  { width: 1280, height: 900 }
]) {
  for (const theme of ['dark', 'light'] as const) {
    test(`search and rail share bounded sticky geometry at ${viewport.width}px in ${theme} (#88)`, async ({}, testInfo) => {
      const browser = await chromium.launch()
      try {
        const page = await browser.newPage({ viewport })
        await page.goto(nearHourArtifactUrl)
        await page.evaluate((selectedTheme) => {
          document.documentElement.dataset.theme = selectedTheme
        }, theme)
        const closed = await page.evaluate(() => {
          const search = document.querySelector<HTMLElement>('.transcript-search')
          const rail = document.querySelector<HTMLElement>('.timeline-nav')
          if (search === null || rail === null) throw new Error('closed geometry missing')
          return {
            searchBottom: search.getBoundingClientRect().bottom,
            railTop: rail.getBoundingClientRect().top,
            overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth
          }
        })
        expect(closed.railTop).toBeGreaterThanOrEqual(closed.searchBottom - 1)
        expect(closed.overflow).toBeLessThanOrEqual(1)
        await page.keyboard.press('/')
        const input = page.getByRole('searchbox', { name: 'Find in transcript' })
        await input.fill('distinct idea')
        await expect(page.getByRole('status')).toContainText('1 of 30')

        const expanded = await page.evaluate(() => {
          const search = document.querySelector<HTMLElement>('.transcript-search')
          const rail = document.querySelector<HTMLElement>('.timeline-nav')
          const active = document.querySelector<HTMLElement>('.search-match-active')
          const controls = Array.from(
            document.querySelectorAll<HTMLElement>('.transcript-search input, .transcript-search button')
          ).filter((item) => !item.hidden)
          if (search === null || rail === null || active === null) throw new Error('search geometry missing')
          const searchRect = search.getBoundingClientRect()
          const railRect = rail.getBoundingClientRect()
          return {
            searchBottom: searchRect.bottom,
            searchHeight: searchRect.height,
            railTop: railRect.top,
            railHeight: railRect.height,
            railBottom: railRect.bottom,
            activeTop: active.getBoundingClientRect().top,
            minControl: Math.min(...controls.map((item) => item.getBoundingClientRect().height)),
            viewport: innerHeight
          }
        })
        expect(expanded.railTop).toBeGreaterThanOrEqual(expanded.searchBottom - 1)
        expect(expanded.activeTop).toBeGreaterThanOrEqual(expanded.railBottom - 1)
        expect(expanded.minControl).toBeGreaterThanOrEqual(44)
        if (viewport.width === 390) {
          const combinedHeight = expanded.searchHeight + expanded.railHeight
          expect(combinedHeight).toBeLessThanOrEqual(expanded.viewport * 0.4)
          expect(expanded.viewport - combinedHeight).toBeGreaterThanOrEqual(500)
        }
        await page.setViewportSize({ width: 390, height: 844 })
        const wrapped = await page.evaluate(() => {
          const search = document.querySelector<HTMLElement>('.transcript-search')
          const rail = document.querySelector<HTMLElement>('.timeline-nav')
          const row = document.querySelector<HTMLElement>('.transcript-search-row')
          const input = document.querySelector<HTMLElement>('.transcript-search-input')
          const previous = document.querySelector<HTMLElement>('.transcript-search-prev')
          if (search === null || rail === null || row === null || input === null || previous === null) {
            throw new Error('wrapped geometry missing')
          }
          return {
            searchBottom: search.getBoundingClientRect().bottom,
            railTop: rail.getBoundingClientRect().top,
            inputBottom: input.getBoundingClientRect().bottom,
            buttonTop: previous.getBoundingClientRect().top,
            overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth
          }
        })
        expect(wrapped.buttonTop).toBeGreaterThanOrEqual(wrapped.inputBottom - 1)
        expect(wrapped.railTop).toBeGreaterThanOrEqual(wrapped.searchBottom - 1)
        expect(wrapped.overflow).toBeLessThanOrEqual(1)
        await page.setViewportSize(viewport)
        await expect(page.getByRole('status')).toContainText('1 of 30')
        await input.evaluate((node) => node.blur())
        const expandedName = `transcript-search-${viewport.width}-${theme}-expanded.png`
        await page.screenshot({
          path: testInfo.outputPath(expandedName),
          fullPage: true,
          caret: 'initial'
        })
        await testInfo.attach(expandedName, {
          path: testInfo.outputPath(expandedName),
          contentType: 'image/png'
        })

        await input.focus()
        for (let index = 0; index < 10; index += 1) await input.press('Enter')
        await expect(page.getByRole('status')).toContainText('11 of 30')
        await expect(page.locator('.timeline-nav')).toHaveClass(/stuck/)
        const stuck = await page.evaluate(() => {
          const search = document.querySelector<HTMLElement>('.transcript-search')
          const rail = document.querySelector<HTMLElement>('.timeline-nav')
          const active = document.querySelector<HTMLElement>('.search-match-active')
          if (search === null || rail === null || active === null) throw new Error('stuck geometry missing')
          return {
            searchBottom: search.getBoundingClientRect().bottom,
            railTop: rail.getBoundingClientRect().top,
            railBottom: rail.getBoundingClientRect().bottom,
            activeTop: active.getBoundingClientRect().top
          }
        })
        expect(stuck.railTop).toBeGreaterThanOrEqual(stuck.searchBottom - 1)
        expect(stuck.activeTop).toBeGreaterThanOrEqual(stuck.railBottom - 1)
        await input.evaluate((node) => node.blur())
        const stuckName = `transcript-search-${viewport.width}-${theme}-stuck.png`
        await page.screenshot({
          path: testInfo.outputPath(stuckName),
          fullPage: true,
          caret: 'initial'
        })
        await testInfo.attach(stuckName, {
          path: testInfo.outputPath(stuckName),
          contentType: 'image/png'
        })
      } finally {
        await browser.close()
      }
    })
  }
}

for (const viewport of [
  { width: 390, height: 844 },
  { width: 1280, height: 900 }
]) {
  for (const theme of ['dark', 'light'] as const) {
    test(`full reader stays usable at ${viewport.width}px in ${theme} theme (#81)`, async ({}, testInfo) => {
      const browser = await chromium.launch()
      try {
        const page = await browser.newPage({ viewport })
        await page.goto(chapteredArtifactUrl)
        await page.evaluate((selectedTheme) => {
          document.documentElement.dataset.theme = selectedTheme
        }, theme)

        const layout = await page.evaluate(() => {
          const content = document.querySelector('#content')
          const heading = document.querySelector('h1')
          const paragraph = document.querySelector('p[data-start]')
          if (content === null || heading === null || paragraph === null) {
            throw new Error('chaptered artifact lacks reader landmarks')
          }
          return {
            viewportWidth: document.documentElement.clientWidth,
            documentWidth: document.documentElement.scrollWidth,
            contentWidth: content.getBoundingClientRect().width,
            headingHeight: heading.getBoundingClientRect().height,
            paragraphHeight: paragraph.getBoundingClientRect().height
          }
        })
        expect(layout.documentWidth).toBeLessThanOrEqual(layout.viewportWidth + 1)
        expect(layout.contentWidth).toBeGreaterThan(0)
        expect(layout.headingHeight).toBeGreaterThan(0)
        expect(layout.paragraphHeight).toBeGreaterThan(0)

        const screenshotName = `full-reader-${viewport.width}-${theme}.png`
        const screenshotPath = testInfo.outputPath(screenshotName)
        await page.screenshot({ path: screenshotPath, fullPage: true })
        await testInfo.attach(screenshotName, {
          path: screenshotPath,
          contentType: 'image/png'
        })
      } finally {
        await browser.close()
      }
    })
  }
}

interface GeometryProbe {
  pad: number
  railHeight: number
  railBottom: number
  targetTop: number
  stuck: boolean
}

for (const viewport of [
  { width: 390, height: 844 },
  { width: 1280, height: 900 }
]) {
  test(`anchor jumps clear the rail at ${viewport.width}px (#63)`, async () => {
    const browser = await chromium.launch()
    try {
      const page = await browser.newPage({ viewport })
      await page.goto(longformArtifactUrl)

      const probe = await page.evaluate(
        () =>
          new Promise<GeometryProbe>((resolve, reject) => {
            const rail = document.querySelector('.timeline-nav')
            const anchors = document.querySelectorAll('p[id^="t-"]')
            if (rail === null || anchors.length === 0) {
              reject(new Error('longform golden lacks a rail or anchors'))
              return
            }
            const target = anchors[Math.floor(anchors.length / 2)]
            if (target === undefined) {
              reject(new Error('no mid-document anchor'))
              return
            }
            location.hash = `#${target.id}`
            // Smooth scrolling: poll until the scroll position is stable
            // across frames instead of guessing a fixed delay.
            let last = -1
            let stableFrames = 0
            const started = performance.now()
            const settle = (): void => {
              if (window.scrollY === last) {
                stableFrames += 1
              } else {
                stableFrames = 0
                last = window.scrollY
              }
              if (stableFrames >= 10 || performance.now() - started > 5000) {
                resolve({
                  pad: parseFloat(getComputedStyle(document.documentElement).scrollPaddingTop),
                  railHeight: rail.getBoundingClientRect().height,
                  railBottom: rail.getBoundingClientRect().bottom,
                  targetTop: target.getBoundingClientRect().top,
                  stuck: rail.classList.contains('stuck')
                })
                return
              }
              requestAnimationFrame(settle)
            }
            requestAnimationFrame(settle)
          })
      )

      // The regression assertion #63 names: offset >= rail height.
      expect(probe.pad).toBeGreaterThanOrEqual(probe.railHeight)
      // And the user-visible truth: the jumped-to passage is not under the rail.
      expect(probe.targetTop).toBeGreaterThanOrEqual(probe.railBottom - 1)
      // Mid-scroll the rail must be in its collapsed state.
      expect(probe.stuck).toBe(true)
    } finally {
      await browser.close()
    }
  })
}

interface DensityProbe {
  linkCount: number
  rowCount: number
  railHeight: number
  allLinksVisible: boolean
}

for (const viewport of [
  { width: 390, height: 844, maxRows: 6 },
  { width: 1280, height: 900, maxRows: 3 }
]) {
  for (const theme of ['dark', 'light'] as const) {
    test(`near-hour rail stays compact at ${viewport.width}px in ${theme} theme (#72)`, async ({}, testInfo) => {
      const browser = await chromium.launch()
      try {
        const page = await browser.newPage({ viewport })
        await page.goto(nearHourArtifactUrl)
        await page.evaluate((selectedTheme) => {
          document.documentElement.dataset.theme = selectedTheme
        }, theme)

        const probe = await page.evaluate((): DensityProbe => {
          const rail = document.querySelector<HTMLElement>('.timeline-nav')
          const links = Array.from(document.querySelectorAll<HTMLElement>('.timeline-links a'))
          if (rail === null) throw new Error('near-hour golden lacks a timeline rail')
          const railRect = rail.getBoundingClientRect()
          const linkRects = links.map((link) => link.getBoundingClientRect())
          const rows = new Set(linkRects.map((rect) => Math.round(rect.top)))
          return {
            linkCount: links.length,
            rowCount: rows.size,
            railHeight: railRect.height,
            allLinksVisible: links.every((link, index) => {
              const rect = linkRects[index]
              const style = getComputedStyle(link)
              return (
                rect !== undefined &&
                style.display !== 'none' &&
                style.visibility !== 'hidden' &&
                rect.width > 0 &&
                rect.height > 0 &&
                rect.left >= railRect.left - 1 &&
                rect.right <= railRect.right + 1 &&
                rect.top >= railRect.top - 1 &&
                rect.bottom <= railRect.bottom + 1
              )
            })
          }
        })

        expect(probe.linkCount).toBe(6)
        expect(probe.rowCount).toBeLessThanOrEqual(viewport.maxRows)
        expect(probe.railHeight).toBeLessThan(viewport.height * 0.25)
        expect(probe.allLinksVisible).toBe(true)
        const screenshotName = `near-hour-rail-${viewport.width}-${theme}.png`
        const screenshotPath = testInfo.outputPath(screenshotName)
        await page.locator('.timeline-nav').screenshot({ path: screenshotPath })
        await testInfo.attach(screenshotName, {
          path: screenshotPath,
          contentType: 'image/png'
        })
      } finally {
        await browser.close()
      }
    })
  }
}

const badgeColors = {
  dark: { background: 'rgb(49, 70, 90)', foreground: 'rgb(242, 246, 248)' },
  light: { background: 'rgb(216, 230, 237)', foreground: 'rgb(41, 73, 90)' }
} as const

for (const theme of ['dark', 'light'] as const) {
  test(`section badges use the reviewed ${theme} palette in both placements (#73)`, async ({}, testInfo) => {
    const browser = await chromium.launch()
    try {
      const page = await browser.newPage({ viewport: { width: 1280, height: 900 } })
      await page.goto(chapteredArtifactUrl)
      await page.evaluate((selectedTheme) => {
        document.documentElement.dataset.theme = selectedTheme
      }, theme)

      for (const selector of [
        '.nav-badge-intro',
        '.nav-badge-outro',
        '.badge-intro',
        '.badge-outro'
      ]) {
        const colors = await page.locator(selector).evaluate((badge) => {
          const style = getComputedStyle(badge)
          return { background: style.backgroundColor, foreground: style.color }
        })
        expect(colors).toEqual(badgeColors[theme])
      }

      const screenshotName = `section-badges-1280-${theme}.png`
      const screenshotPath = testInfo.outputPath(screenshotName)
      await page.screenshot({ path: screenshotPath })
      await testInfo.attach(screenshotName, {
        path: screenshotPath,
        contentType: 'image/png'
      })
    } finally {
      await browser.close()
    }
  })
}
