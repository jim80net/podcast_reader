#!/usr/bin/env node
/**
 * Installer build wrapper (design decision 9): accepts the `--engine-dir`
 * build input and forwards everything else to electron-builder.
 *
 *   node scripts/dist.mjs [--engine-dir <frozen-onedir>] [electron-builder args]
 *
 * `--engine-dir` points at a frozen engine onedir (spike layout:
 * `podcast-reader-engine[.exe]`, sibling `whisper-worker`, shared
 * `_internal/`); it is mapped uncompressed into `<resources>/engine/` via
 * extraResources (`electron-builder.config.cjs`). Omitting it produces a
 * valid engine-less build that uses the app-shell spawn chain.
 */
import { spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { createRequire } from 'node:module'
import { resolve } from 'node:path'

const args = process.argv.slice(2)
const forwarded = []
let engineDir
for (let i = 0; i < args.length; i++) {
  if (args[i] === '--engine-dir') {
    engineDir = args[i + 1]
    if (engineDir === undefined) {
      console.error('--engine-dir requires a path argument')
      process.exit(2)
    }
    i += 1
    continue
  }
  forwarded.push(args[i])
}

const env = { ...process.env }
if (engineDir !== undefined) {
  const dir = resolve(engineDir)
  if (!existsSync(dir)) {
    console.error(`engine dir does not exist: ${dir}`)
    process.exit(2)
  }
  env.PODCAST_READER_ENGINE_DIR = dir
} else {
  delete env.PODCAST_READER_ENGINE_DIR
  console.log('no --engine-dir given: building without a packaged engine (dev spawn chain applies)')
}

const require = createRequire(import.meta.url)
const cli = require.resolve('electron-builder/cli.js')
const result = spawnSync(
  process.execPath,
  [cli, '--config', 'electron-builder.config.cjs', ...forwarded],
  { stdio: 'inherit', env }
)
process.exit(result.status ?? 1)
