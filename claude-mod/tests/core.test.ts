import { describe, expect, test } from 'claude-code/testing'

import type { Seg, Snapshot, TimelineRow } from '../types'
import {
  eventName,
  hhmmss,
  innerWidth,
  isSidebarActive,
  liveArgv,
  nameRole,
  parseLayout,
  parseRun,
  rowTag,
  shortTokens,
  sidebarHeight,
  sidebarName,
  spawnFailed,
  timelineLine,
  withLastNames,
  withoutLabelLanes,
  withoutOccupancy,
} from '../hooks/core'

const row = (no: number, over: Partial<TimelineRow>): TimelineRow => ({
  no, ts: null, kind: 'action', component: null, transition: null, detail: 'x',
  confidence: 'exact', est_tokens: 0, ctx_tokens: null, sidechain: false, ...over,
})

const snap = (over: Partial<Snapshot> = {}): Snapshot => ({
  schema_version: 4, session_id: 'sess-1', cc_version: '2.1.288', width: 56,
  map: Array.from({ length: 10 }, () => [{ t: 'x', role: null, no: null }]),
  axis: [{ t: '14:00:00', role: 'dim', no: null }],
  last: { load: 3, action: 4 },
  vertical: null,
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
  test('liveArgv appends --height for the sidebar map', () => {
    expect(liveArgv('ctxr', 'abc', 40, 26)).toEqual([
      'ctxr', 'live', 'abc', '--json', '--width', '40', '--height', '26',
    ])
    expect(liveArgv('ctxr', 'abc', 40)).not.toContain('--height')
  })
})

describe('parseRun', () => {
  test('schema 3 (an older ctxr) is refused', () => {
    const out = parseRun(0, JSON.stringify(snap({ schema_version: 3 })), '')
    expect(out.kind === 'error' && out.message).toMatch(/schema 3 not supported/)
  })
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
    const out = parseRun(0, JSON.stringify(snap({ schema_version: 5 })), '')
    expect(out.kind === 'error' && out.message).toMatch(/schema 5 not supported/)
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

describe('withLastNames', () => {
  const seg = (t: string, role: Seg['role'] = null): Seg => ({ t, role, no: null })
  const legend = [seg('  '), seg('context window map', 'bold')]
  const loads = [seg('  loads    '), seg('▼ ▼', 'load')]
  const box = [seg('          '), seg('│▅ █│', 'dim')]
  const actions = [seg('  actions  '), seg('▲ ▲', 'action')]
  test('names the event behind each arrow row\'s rightmost mark', () => {
    expect(withLastNames([legend, loads, box, actions], snap())).toEqual([
      legend,
      [...loads, seg(' Read a.md ~', 'load')],
      box,
      [...actions, seg(' Bash pytest', 'action')],
    ])
  })
  test('a side without a mark, or an unknown row, stays bare', () => {
    const map = [legend, loads, box, actions]
    expect(withLastNames(map, snap({ last: { load: null, action: 99 } }))).toEqual(map)
  })
})

describe('eventName', () => {
  test('component id, tool + target for actions, else the detail', () => {
    expect(eventName(row(2, { kind: 'claude_md', transition: 'loaded', component: 'claude-md:root' })))
      .toBe('claude-md:root')
    expect(eventName(row(4, { kind: 'action', tool: 'Bash', detail: 'git status' }))).toBe('Bash git status')
    expect(eventName(row(4, { kind: 'action', tool: 'Edit', detail: 'README.md' }))).toBe('Edit README.md')
    expect(eventName(row(3, { kind: 'file_read', detail: 'Read a.md' }))).toBe('Read a.md')
  })
  test('heuristic rows carry the ~ mark', () => {
    expect(eventName(row(5, { kind: 'hook', transition: 'invoked', component: 'hook:SessionStart', confidence: 'heuristic' })))
      .toBe('hook:SessionStart ~')
  })
})

describe('sidebar helpers', () => {
  test('parseLayout: only "sidebar" is the sidebar', () => {
    expect(parseLayout('sidebar')).toBe('sidebar')
    expect(parseLayout('bottom')).toBe('bottom')
    expect(parseLayout('right')).toBe('bottom')
    expect(parseLayout(undefined)).toBe('bottom')
  })
  test('sidebarName drops the component kind prefix', () => {
    expect(sidebarName(row(1, { kind: 'claude_md', transition: 'loaded', component: 'claude-md:CLAUDE.md' }))).toBe('CLAUDE.md')
    expect(sidebarName(row(1, { kind: 'skill', transition: 'invoked', component: 'skill:finishing-a-development-branch' }))).toBe('finishing-a-dev')
    expect(sidebarName(row(1, { kind: 'mcp', transition: 'invoked', component: 'plain' }))).toBe('plain')
  })
  test('sidebarName keeps the action target: no tool, no leading assignments, basenames', () => {
    expect(sidebarName(row(1, { tool: 'Bash', detail: 'D=/private/tmp/x SP=/p sed -n 1,20p /repo/a/core.ts' }))).toBe('sed -n 1,20p co')
    expect(sidebarName(row(1, { tool: 'Edit', detail: '/repo/claude-mod/hooks/view.tsx' }))).toBe('view.tsx')
    expect(sidebarName(row(1, { tool: 'Bash', detail: 'ls docs/' }))).toBe('ls docs/')
  })
  test('sidebarName falls back to the raw name when only assignments remain', () => {
    expect(sidebarName(row(1, { tool: 'Bash', detail: 'FOO=1' }))).toBe('Bash FOO=1')
  })
  test('sidebarName keeps other details as they are', () => {
    expect(sidebarName(row(1, { kind: 'file_read', detail: 'Read /a/b.md' }))).toBe('Read /a/b.md')
    expect(sidebarName(row(1, { kind: 'compaction', detail: 'compacted' }))).toBe('compacted')
  })
  test('sidebarName cuts at 15 code points, then marks heuristic rows', () => {
    expect(sidebarName(row(1, { kind: 'file_read', detail: 'Read a-very-long-file-name.md', confidence: 'heuristic' }))).toBe('Read a-very-lon ~')
    expect(sidebarName(row(1, { kind: 'file_read', detail: '讀取一個非常長的中文檔案名稱的文件內容' }))).toBe('讀取一個非常長的中文檔案名稱的')
    expect(Array.from(sidebarName(row(1, { kind: 'file_read', detail: '😀'.repeat(20) })))).toHaveLength(15)
  })
  test('nameRole follows the event side', () => {
    expect(nameRole(row(1, { kind: 'compaction' }))).toBe('compaction')
    expect(nameRole(row(1, { kind: 'file_read' }))).toBe('load')
    expect(nameRole(row(1, { kind: 'skill', transition: 'loaded' }))).toBe('load')
    expect(nameRole(row(1, { kind: 'skill', transition: 'invoked' }))).toBe('action')
    expect(nameRole(row(1, { kind: 'action' }))).toBe('action')
  })
  test('sidebarHeight leaves room for the header and borders', () => {
    expect(sidebarHeight(30)).toBe(26)
    expect(sidebarHeight(5)).toBe(1)
    expect(sidebarHeight(4)).toBeNull()
    expect(sidebarHeight(null)).toBeNull()
    expect(sidebarHeight(500)).toBe(200)
  })
  test('isSidebarActive needs sidebar layout, a shown placed pane, and a dock', () => {
    const pane = { isShown: true, isPlaced: true }
    expect(isSidebarActive('sidebar', pane, 'dock')).toBe(true)
    expect(isSidebarActive('bottom', pane, 'dock')).toBe(false)
    expect(isSidebarActive('sidebar', undefined, 'dock')).toBe(false)
    expect(isSidebarActive('sidebar', { ...pane, isShown: false }, 'dock')).toBe(false)
    expect(isSidebarActive('sidebar', { ...pane, isPlaced: false }, 'dock')).toBe(false)
    expect(isSidebarActive('sidebar', pane, 'inline')).toBe(false)
    expect(isSidebarActive('sidebar', pane, null)).toBe(false)
  })
})
