import { spawn } from 'node:child_process'

import { describe, expect, it } from 'vitest'

import { parseGuardianEvent, SpawnedGuardian } from './serve-process'

describe('parseGuardianEvent', () => {
  it('accepts only the bounded protocol vocabulary', () => {
    expect(parseGuardianEvent('{"event":"bound","target":"http://127.0.0.1:1"}')).toEqual({
      event: 'bound',
      target: 'http://127.0.0.1:1'
    })
    expect(parseGuardianEvent('{"event":"stopped"}')).toEqual({ event: 'stopped' })
    expect(
      parseGuardianEvent('{"event":"unowned","severity":"conflict","message":"occupied"}')
    ).toEqual({
      event: 'unowned',
      severity: 'conflict',
      message: 'occupied'
    })
  })

  it.each([
    '{',
    '[]',
    '{"event":"future"}',
    '{"event":"bound","target":1}',
    '{"event":"stopped","secret":"no"}',
    JSON.stringify({ event: 'error', message: 'x'.repeat(5000) })
  ])('rejects malformed, novel, or oversized event %s', (line) => {
    expect(parseGuardianEvent(line)).toBeNull()
  })
})

describe('SpawnedGuardian lease pipe', () => {
  it('turns a real child-side pipe close into a protocol failure, not an uncaught EPIPE', async () => {
    const child = spawn(
      process.execPath,
      ['-e', 'process.stdin.destroy(); setTimeout(() => process.exit(0), 250)'],
      { stdio: ['pipe', 'pipe', 'pipe'] }
    )
    const guardian = new SpawnedGuardian(child, () => undefined)
    await new Promise((resolve) => setTimeout(resolve, 50))
    guardian.sendGo()
    await expect(guardian.nextEvent(1000)).rejects.toThrow(/guardian/)
    if (child.exitCode === null) child.kill('SIGKILL')
  })
})
