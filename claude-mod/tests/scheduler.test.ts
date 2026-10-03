import { expect, test } from 'claude-code/testing'

import { Scheduler } from '../hooks/scheduler'

function fakeTimers() {
  let now = 0
  let pending: { at: number; fn: () => void; isCancelled: boolean }[] = []
  return {
    after(ms: number, fn: () => void) {
      const timer = { at: now + ms, fn, isCancelled: false }
      pending.push(timer)
      return { cancel: () => { timer.isCancelled = true } }
    },
    async advance(ms: number) {
      now += ms
      const due = pending.filter(t => !t.isCancelled && t.at <= now)
      pending = pending.filter(t => !t.isCancelled && t.at > now)
      for (const t of due) t.fn()
      for (let i = 0; i < 5; i++) await Promise.resolve()
    },
  }
}

function gatedRun() {
  let runs = 0
  let release: (() => void) | null = null
  return {
    get runs() { return runs },
    run: () => { runs += 1; return new Promise<void>(resolve => { release = resolve }) },
    async finish() { release?.(); release = null; for (let i = 0; i < 5; i++) await Promise.resolve() },
  }
}

test('pokes inside the debounce window collapse into one run', async () => {
  const timers = fakeTimers()
  const gate = gatedRun()
  const s = new Scheduler({ after: timers.after, run: gate.run }, 400)
  s.poke(); await timers.advance(100); s.poke(); await timers.advance(100); s.poke()
  await timers.advance(399)
  expect(gate.runs).toBe(0)
  await timers.advance(1)
  expect(gate.runs).toBe(1)
})

test('pokes during a run cause exactly one follow-up run, never an overlap', async () => {
  const timers = fakeTimers()
  const gate = gatedRun()
  const s = new Scheduler({ after: timers.after, run: gate.run }, 400)
  s.poke(); await timers.advance(400)
  expect(gate.runs).toBe(1)
  s.poke(); s.poke(); s.poke()
  await timers.advance(1000)
  expect(gate.runs).toBe(1) // still running: no second process
  await gate.finish()
  await timers.advance(400)
  expect(gate.runs).toBe(2)
  await gate.finish()
  await timers.advance(1000)
  expect(gate.runs).toBe(2)
})
