import { describe, expect, test } from 'claude-code/testing'

import type { Snapshot, TimelineRow } from '../types'
import {
  eventLine,
  hhmmss,
  innerWidth,
  liveArgv,
  parseRun,
  planBand,
  recentEvents,
  rowTag,
  shortTokens,
  spawnFailed,
  timelineLine,
} from '../hooks/core'

const row = (no: number, over: Partial<TimelineRow>): TimelineRow => ({
  no, ts: null, kind: 'action', component: null, transition: null, detail: 'x',
  confidence: 'exact', est_tokens: 0, ctx_tokens: null, sidechain: false, ...over,
})

const snap = (over: Partial<Snapshot> = {}): Snapshot => ({
  schema_version: 1, session_id: 'sess-1', cc_version: '2.1.288', width: 56,
  map: Array.from({ length: 10 }, () => [{ t: 'x', role: null, no: null }]),
  axis: [{ t: '14:00:00', role: 'dim', no: null }],
  timeline: [
    row(1, { kind: 'session_start' }),
    row(2, { kind: 'claude_md', transition: 'loaded', component: 'claude-md:root', est_tokens: 739 }),
    row(3, { kind: 'file_read', detail: 'Read a.md', confidence: 'heuristic', est_tokens: 1200 }),
    row(4, { kind: 'action', detail: 'Bash pytest' }),
    row(5, { kind: 'file_read', detail: '[subagent:x] Read b.md', sidechain: true }),
  ],
  occupancy: { current: 1, peak: 1, window: 200000 },
  warnings: [],
  ...over,
})

describe('width and argv', () => {
  test('innerWidth leaves room for gutter, borders and the right legend', () => {
    expect(innerWidth(120)).toBe(76)
    expect(innerWidth(64)).toBe(20)
    expect(innerWidth(10)).toBe(20)
    expect(innerWidth(1000)).toBe(200)
  })
  test('liveArgv splits the configured command', () => {
    expect(liveArgv('env PYTHONPATH=/r  /r/py -m context_render.cli', 'abc', 40)).toEqual([
      'env', 'PYTHONPATH=/r', '/r/py', '-m', 'context_render.cli',
      'live', 'abc', '--json', '--width', '40',
    ])
    expect(liveArgv('   ', 'abc', 40)[0]).toBe('ctxr')
  })
})

describe('parseRun', () => {
  test('ok snapshot', () => {
    const out = parseRun(0, JSON.stringify(snap()), '')
    expect(out.kind).toBe('ok')
  })
  test('precondition error shows stderr first line without the Error: prefix', () => {
    expect(parseRun(3, '', '\nError: manifest not found; run ctxr init first\n')).toEqual({
      kind: 'error', message: 'ctxr-live: manifest not found; run ctxr init first',
    })
  })
  test('other exit codes and garbage keep a debug payload', () => {
    const crash = parseRun(1, '', 'Traceback ...')
    expect(crash.kind === 'error' && crash.debug).toBe('Traceback ...')
    const garbage = parseRun(0, 'not json', '')
    expect(garbage.kind === 'error' && garbage.message).toBe('ctxr-live: unreadable ctxr output')
  })
  test('unknown schema version is refused', () => {
    const out = parseRun(0, JSON.stringify(snap({ schema_version: 2 })), '')
    expect(out.kind === 'error' && out.message).toMatch(/schema 2 not supported/)
  })
  test('spawn failure names the command', () => {
    const out = spawnFailed('ctxr', new Error('ENOENT'))
    expect(out.kind === 'error' && out.message).toMatch(/could not run ctxr/)
  })
})

describe('event lines', () => {
  test('recentEvents keeps the last main-window loads and actions', () => {
    expect(recentEvents(snap().timeline).map(r => r.no)).toEqual([2, 3, 4])
    expect(recentEvents(snap().timeline, 1).map(r => r.no)).toEqual([4])
  })
  test('eventLine', () => {
    const [l, r, a] = recentEvents(snap().timeline)
    expect(eventLine(l!)).toBe('#2 ▼ L claude-md:root  exact  739 tok')
    expect(eventLine(r!)).toBe('#3 ▼ read Read a.md  heuristic  1.2k tok')
    expect(eventLine(a!)).toBe('#4 ▲ act Bash pytest  exact')
  })
  test('rowTag covers non-event rows', () => {
    expect(rowTag(row(1, { kind: 'session_start' }))).toBe('start')
    expect(rowTag(row(1, { kind: 'compaction' }))).toBe('⟐')
    expect(rowTag(row(1, { kind: 'skill', transition: 'invoked' }))).toBe('I')
  })
  test('shortTokens mirrors the Python _short', () => {
    expect(shortTokens(999)).toBe('999')
    expect(shortTokens(1000)).toBe('1k')
    expect(shortTokens(143_700)).toBe('143.7k')
    expect(shortTokens(1_500_000)).toBe('1.5M')
  })
  test('hhmmss and timelineLine', () => {
    expect(hhmmss(null)).toBe('--:--:--')
    expect(hhmmss('garbage')).toBe('--:--:--')
    expect(timelineLine(row(3, { kind: 'file_read', detail: 'Read a.md', confidence: 'heuristic', ctx_tokens: 12_000 })))
      .toBe('  3 --:--:--    12k ─ read ~ Read a.md')
  })
})

describe('planBand degrades by maxRows', () => {
  test('everything fits', () => {
    const p = planBand(snap(), 20, false)
    expect([p.map.length, p.axis !== null, p.recent.length]).toEqual([10, true, 3])
  })
  test('drops recent lines first, then the axis', () => {
    expect(planBand(snap(), 11, false).recent).toEqual([])
    expect(planBand(snap(), 11, false).axis).not.toBeNull()
    expect(planBand(snap(), 10, false).axis).toBeNull()
    expect(planBand(snap(), 11, true).axis).toBeNull() // the warnings line takes a row
  })
})
