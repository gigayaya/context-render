import { describe, expect, test } from 'claude-code/testing'

import type { Seg, Snapshot, TimelineRow } from '../types'
import {
  hhmmss,
  innerWidth,
  liveArgv,
  parseRun,
  rowTag,
  shortTokens,
  spawnFailed,
  timelineLine,
  withoutLabelLanes,
  withoutOccupancy,
} from '../hooks/core'

const row = (no: number, over: Partial<TimelineRow>): TimelineRow => ({
  no, ts: null, kind: 'action', component: null, transition: null, detail: 'x',
  confidence: 'exact', est_tokens: 0, ctx_tokens: null, sidechain: false, ...over,
})

const snap = (over: Partial<Snapshot> = {}): Snapshot => ({
  schema_version: 2, session_id: 'sess-1', cc_version: '2.1.288', width: 56,
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
  notice: null,
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
    const out = parseRun(0, JSON.stringify(snap({ schema_version: 3 })), '')
    expect(out.kind === 'error' && out.message).toMatch(/schema 3 not supported/)
  })
  test('spawn failure names the command', () => {
    const out = spawnFailed('ctxr', new Error('ENOENT'))
    expect(out.kind === 'error' && out.message).toMatch(/could not run ctxr/)
  })
})

describe('timeline lines', () => {
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

describe('withoutOccupancy', () => {
  test('leaves out the occupancy bar (window row and its borders)', () => {
    const seg = (t: string, role: Seg['role'] = null): Seg => ({ t, role, no: null })
    const box = [seg('          '), seg('└──┘', 'dim')]
    const map = [
      [seg('  '), seg('context window map', 'bold')],
      box,
      [seg('          '), seg('┌──┐', 'dim')],
      [seg('  window  '), seg('│', 'dim'), seg('▂', 'occupancy'), seg('│', 'dim')],
      [seg('          '), seg('└──┘', 'dim')],
    ]
    expect(withoutOccupancy(map)).toEqual(map.slice(0, 2))
    expect(withoutOccupancy(map.slice(0, 2))).toEqual(map.slice(0, 2))
  })
})

describe('withoutLabelLanes', () => {
  const seg = (t: string, role: Seg['role'] = null, no: number | null = null): Seg => ({ t, role, no })
  const lane = (...nos: number[]) => [seg('           '), ...nos.map(n => seg(String(n), 'load', n))]
  const legend = [seg('  '), seg('context window map', 'bold')]
  const loads = [seg('  loads    '), seg('▼ ▼', 'load')]
  const box = [seg('          '), seg('│▅ █│', 'dim')]
  const actions = [seg('  actions  '), seg('▲ ▲', 'action')]
  test('drops every row that carries timeline numbers', () => {
    const map = [legend, lane(10), lane(2, 7), loads, box, actions, lane(4, 8), lane(5)]
    expect(withoutLabelLanes(map)).toEqual([legend, loads, box, actions])
  })
})
