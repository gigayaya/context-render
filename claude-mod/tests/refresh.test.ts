import { expect, mock, test } from 'claude-code/testing'
import type { On } from 'claude-code'

import { memState, sessionStart } from './engine'

const SNAP = {
  schema_version: 3, session_id: 'sess-1', cc_version: '2.1.288', width: 56,
  map: [[{ t: '  ', role: null, no: null }, { t: 'context window map', role: 'bold', no: null }]],
  axis: null,
  last: { load: null, action: null },
  timeline: [{ no: 1, ts: null, kind: 'session_start', component: null, transition: null,
               detail: 'cc', confidence: 'exact', est_tokens: 0, ctx_tokens: null, sidechain: false }],
  occupancy: { current: null, peak: null, window: 200000 },
  warnings: [],
  notice: null,
}

type Reply = { exitCode: number; stdout: string; stderr: string } | 'reject' | 'timeout'

function engine(on: On, opts: { sessionId?: () => string; reply?: () => Reply; gate?: Promise<void> } = {}) {
  const clock = mock.clock(on)
  const state = memState(on)
  sessionStart(on)
  const runs: string[][] = []
  on('process.run', async ($, e) => {
    runs.push([...e.argv])
    const reply = opts.reply?.() ?? { exitCode: 0, stdout: JSON.stringify(SNAP), stderr: '' }
    if (reply === 'reject') throw new Error('ENOENT')
    if (reply === 'timeout') {
      await opts.gate // the test moves the clock past the deadline, then lets it reject
      throw new Error('killed')
    }
    return { value: { ...reply, isStdoutTruncated: false, isStderrTruncated: false } }
  })
  on('session.id', () => ({ value: (opts.sessionId ?? (() => 'sess-1'))() }))
  on('tool.call', () => ({ result: 'tool ran', text: 'tool ran' }))
  on('ui.log', () => ({ value: undefined }))
  return { clock, runs, state }
}

const START = { cwd: '/repo', surface: 'terminal', isInteractive: true } as const
const snapOf = (state: { get: (key: string) => unknown }) =>
  state.get('snapshot') as { session_id: string } | null | undefined

test('session.start schedules one refresh with the session id and width', async ($, on) => {
  const { clock, runs, state } = engine(on)
  await $.session.start(START)
  await clock.advance(400)
  expect(runs).toEqual([['ctxr', 'live', 'sess-1', '--json', '--width', '56']])
  expect(snapOf(state)?.session_id).toBe('sess-1')
  expect(state.get('isRefreshing')).toBe(false)
})

test('a non-interactive session never runs ctxr', async ($, on) => {
  const { clock, runs } = engine(on)
  await $.session.start({ ...START, surface: null, isInteractive: false })
  await clock.advance(5000)
  expect(runs).toEqual([])
})

test('tool calls pass through untouched and refresh once per quiet window', async ($, on) => {
  const { clock, runs } = engine(on)
  await $.session.start(START)
  await clock.advance(400)
  const results = [
    await $.tool.call({ tool: 'Read', file_path: '/repo/a.md' }),
    await $.tool.call({ tool: 'Read', file_path: '/repo/b.md' }),
    await $.tool.call({ tool: 'Read', file_path: '/repo/c.md' }),
  ]
  expect(results.map(r => r.text)).toEqual(['tool ran', 'tool ran', 'tool ran'])
  await clock.advance(400)
  expect(runs.length).toBe(2)
})

test('the configured command is used', { options: { command: 'env PYTHONPATH=/r py -m context_render.cli' } },
  async ($, on) => {
    const { clock, runs } = engine(on)
    await $.session.start(START)
    await clock.advance(400)
    expect(runs[0]!.slice(0, 5)).toEqual(['env', 'PYTHONPATH=/r', 'py', '-m', 'context_render.cli'])
  })

test('an error keeps the last snapshot of the same session (stale)', async ($, on) => {
  let fail = false
  const { clock, state } = engine(on, {
    reply: () => (fail ? { exitCode: 1, stdout: '', stderr: 'boom' } : { exitCode: 0, stdout: JSON.stringify(SNAP), stderr: '' }),
  })
  await $.session.start(START)
  await clock.advance(400)
  fail = true
  await $.tool.call({ tool: 'Read', file_path: '/repo/a.md' })
  await clock.advance(400)
  expect(snapOf(state)?.session_id).toBe('sess-1')
  expect(state.get('error')).toBe('ctxr-live: ctxr exited 1')
})

test('clears a snapshot of another session on error (after /clear or resume)', async ($, on) => {
  let id = 'sess-1'
  let fail = false
  const { clock, state } = engine(on, {
    sessionId: () => id,
    reply: () => (fail ? { exitCode: 3, stdout: '', stderr: "Error: No session found with id prefix 'sess-2'" } : { exitCode: 0, stdout: JSON.stringify(SNAP), stderr: '' }),
  })
  await $.session.start(START)
  await clock.advance(400)
  id = 'sess-2'
  fail = true
  await $.tool.call({ tool: 'Read', file_path: '/repo/a.md' })
  await clock.advance(400)
  expect(state.get('snapshot')).toBeNull()
  expect(state.get('error')).toMatch(/No session found/)
})

test('a command that cannot start becomes a one-line error', async ($, on) => {
  const { clock, state } = engine(on, { reply: () => 'reject' })
  await $.session.start(START)
  await clock.advance(400)
  expect(state.get('error')).toMatch(/could not run ctxr/)
  expect(state.get('isRefreshing')).toBe(false)
})

test('session.start resets a stuck isRefreshing (reload mid-refresh)', async ($, on) => {
  const { clock, state } = engine(on)
  state.set('isRefreshing', true)
  await $.session.start(START)
  expect(state.get('isRefreshing')).toBe(false)
  await clock.advance(400)
})

test('a timeout is not reported as a command that cannot start', async ($, on) => {
  let release = () => {}
  const gate = new Promise<void>(resolve => { release = resolve })
  const { clock, state } = engine(on, { reply: () => 'timeout', gate })
  await $.session.start(START)
  await clock.advance(400) // the run starts and hangs
  await clock.advance(5000) // past TIMEOUT_MS
  release()
  for (let i = 0; i < 5; i++) await clock.advance(0)
  expect(state.get('error')).toMatch(/timed out/)
  expect(state.get('error')).not.toMatch(/could not run/)
  expect(state.get('isRefreshing')).toBe(false)
})
