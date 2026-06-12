import { defineConfig } from '@playwright/test'

/**
 * Playwright e2e for the extension (task 8.1, design decision 12): the
 * REAL built extension (`dist/`, via `npm run build`) loaded into a
 * persistent Chromium context with `--load-extension`, against the app's
 * scriptable mock engine (`../app/tests/mock-engine/server.ts`) spawned as
 * a separate process — one fake for both consumers.
 *
 * Run `npm run build` first; on headless hosts wrap in `xvfb-run -a`
 * (extensions need a headed Chromium). One worker: each test boots its own
 * browser profile + mock engine — serial keeps xvfb calm.
 */
export default defineConfig({
  testDir: 'tests/e2e',
  timeout: 60_000,
  workers: 1,
  retries: process.env.CI !== undefined ? 1 : 0,
  reporter: [['list']]
})
