import { expect, mock, test } from 'claude-code/testing'
import type { On } from 'claude-code'

import { engineDraws, memState } from './engine'

const SIDEBAR = { options: { layout: 'sidebar' } }
const seg = (t: string, role: string | null = null, no: number | null = null) => ({ t, role, no })
const ROW = (no: number, over: Record<string, unknown>) => ({
  no, ts: null, kind: 'action', component: null, transition: null, detail: 'x',
  confidence: 'exact', est_tokens: 0, ctx_tokens: null, sidechain: false, ...over,
})
const SNAP = {
  schema_version: 4, session_id: 'sess-1', cc_version: '2.1.288', width: 56,
  map: [[seg('  '), seg('context window map', 'bold')], [seg('  loads    '), seg('▼', 'load')]],
  axis: null,
  last: { load: 2, action: 3 },
  timeline: [
    ROW(1, { kind: 'session_start', detail: 'cc 2.1.288' }),
    ROW(2, { kind: 'claude_md', transition: 'loaded', component: 'claude-md:CLAUDE.md', detail: 'root CLAUDE.md' }),
    ROW(3, { kind: 'action', tool: 'Bash', detail: 'SP=/tmp/x git add /repo/claude-mod/hooks/view.tsx', confidence: 'heuristic' }),
  ],
  vertical: {
    rows: [
      [seg(' '), seg('ctx map', 'bold')],
      [seg(' '), seg('▶', 'action'), seg(' act ', 'dim'), seg('◀', 'load'), seg(' inj', 'dim')],
      [seg(' '), seg('┌──┐', 'dim')],
      [seg(' '), seg('│', 'dim'), seg(' '), seg('█', 'load'), seg('│', 'dim'), seg(' '), seg(' '), seg('◀', 'load')],
      [seg(' '), seg('│', 'dim'), seg('▏', 'action'), seg(' '), seg('│', 'dim'), seg(' '), seg('▶', 'action'), seg(' ')],
      [seg(' '), seg('└──┘', 'dim')],
    ],
    row_no: [null, null, null, 2, 3, null],
  },
  occupancy: { current: null, peak: null, window: 200000 },
  warnings: [] as string[],
  notice: null as string | null,
}
type Reply = { exitCode: number; stdout: string; stderr: string }
const OK = (snap: unknown = SNAP) => (): Reply => ({ exitCode: 0, stdout: JSON.stringify(snap), stderr: '' })
type Pane = { id: string; title: string; isShown: boolean; isFocused: boolean; isPlaced: boolean }

const MAP = (over: Record<string, unknown> = {}) => ({
  component: 'Pane' as const,
  requestId: 'ctxr-map',
  props: {
    title: 'context map', isFocused: false, bodyColumns: 26, placement: 'dock' as const,
    scroll: { offset: 0, bodyRows: 30 }, view: {}, ...over,
  },
})
const BAND = () => ({
  component: 'AbovePrompt' as const,
  props: { hasSurvey: false, isWorking: false, maxRows: 30, bodyColumns: 100, scroll: { offset: 0, bodyRows: 30 }, view: {} },
})

async function ready($: any, on: On, reply: () => Reply = OK()) {
  const clock = mock.clock(on)
  const state = memState(on)
  const opened: { id: string; columns?: number }[] = []
  const registered: string[] = []
  const runs: string[][] = []
  let panes: Pane[] = []
  engineDraws(on)
  on('session.start', ($$, e) => ({ cwd: e.cwd }))
  on('command.register', ($$, e) => { registered.push(e.name); return { value: { command: e.name } } })
  on('ui.open', ($$, e) => { opened.push(e); return { value: { isPlaced: true as const } } })
  on('ui.panes', () => ({ value: panes }))
  on('ui.close', () => ({ value: undefined }))
  on('tool.call', () => ({ result: 'ok', text: 'ok' }))
  on('process.run', ($$, e) => {
    runs.push([...e.argv])
    return { value: { ...reply(), isStdoutTruncated: false, isStderrTruncated: false } }
  })
  on('session.id', () => ({ value: 'sess-1' }))
  on('ui.log', () => ({ value: undefined }))
  await $.session.start({ cwd: '/repo', surface: 'terminal', isInteractive: true })
  await clock.advance(400)
  return {
    clock, state, opened, registered, runs,
    showPane: (over: Partial<Pane> = {}) => {
      panes = [{ id: 'ctxr-map', title: 'context map', isShown: true, isFocused: false, isPlaced: true, ...over }]
    },
    hidePanes: () => { panes = [] },
  }
}

test('sidebar mode opens the map pane and registers /ctx-map', SIDEBAR, async ($, on) => {
  const { opened, registered } = await ready($, on)
  expect(opened).toEqual([expect.objectContaining({ id: 'ctxr-map', title: 'context map', columns: 26 })])
  expect(registered).toEqual(['ctx', 'ctx-map'])
})

test('bottom mode (the default) opens no map pane', async ($, on) => {
  const { opened, registered } = await ready($, on)
  expect(opened).toEqual([])
  expect(registered).toEqual(['ctx'])
})

test('an unknown layout value means bottom', { options: { layout: 'right' } }, async ($, on) => {
  const { opened, registered } = await ready($, on)
  expect(opened).toEqual([])
  expect(registered).toEqual(['ctx'])
})

test('/ctx-map reopens the map pane', SIDEBAR, async ($, on) => {
  const { opened } = await ready($, on)
  const out = await $.command.run({
    command: 'ctx-map', args: '', origin: { kind: 'composer' },
    presentation: { isFullscreen: true, columns: 160 },
  })
  expect(opened).toHaveLength(2)
  expect(opened[1]).toEqual(expect.objectContaining({ id: 'ctxr-map', columns: 26 }))
  expect(out.text).toMatch(/context map/)
})

test('the docked pane draws the vertical map with cleaned names', SIDEBAR, async ($, on) => {
  await ready($, on)
  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ plugin: 'ctxr-live', surface, ...MAP() })
    expect(await ui.find({ type: 'Text', text: 'ctx map' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: ' CLAUDE.md' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: ' git add view.ts ~' })).toBeDefined()
    await ui.unmount()
  }
})

test('the docked pane records its placement', SIDEBAR, async ($, on) => {
  const { clock, state } = await ready($, on)
  const ui = await $.ui.mount({ plugin: 'ctxr-live', surface: 'terminal', ...MAP() })
  await clock.advance(1) // a render hook cannot write state: the seat lands on the next tick
  expect(state.get('placement')).toBe('dock')
  await ui.unmount()
})

test('an inline pane draws a one-line hint instead of the map', SIDEBAR, async ($, on) => {
  const { clock, state } = await ready($, on)
  const ui = await $.ui.mount({ plugin: 'ctxr-live', surface: 'terminal', ...MAP({ placement: 'inline' }) })
  await clock.advance(1)
  expect(await ui.find({ type: 'Text', text: /sidebar needs fullscreen/ })).toBeDefined()
  expect(await ui.find({ type: 'Text', text: 'ctx map' })).toBeUndefined()
  expect(state.get('placement')).toBe('inline')
  await ui.unmount()
})

test('the docked pane without a snapshot shows the error', SIDEBAR, async ($, on) => {
  await ready($, on, OK({ ...SNAP, schema_version: 3 }))
  const ui = await $.ui.mount({ plugin: 'ctxr-live', surface: 'terminal', ...MAP() })
  expect(await ui.find({ type: 'Text', text: /schema 3 not supported/ })).toBeDefined()
  await ui.unmount()
})

test('the docked pane with no vertical map yet says so', SIDEBAR, async ($, on) => {
  await ready($, on, OK({ ...SNAP, vertical: null }))
  const ui = await $.ui.mount({ plugin: 'ctxr-live', surface: 'terminal', ...MAP() })
  expect(await ui.find({ type: 'Text', text: 'ctxr-live: no map yet' })).toBeDefined()
  await ui.unmount()
})

async function bandShowsMap($: any): Promise<boolean> {
  const ui = await $.ui.mount({ plugin: 'ctxr-live', surface: 'terminal', ...BAND() })
  const found = await ui.find({ type: 'Text', text: /context window map/ })
  await ui.unmount()
  return found !== undefined
}

// the pane render records its seat one clock tick later (a render hook cannot write state)
async function seat($: any, clock: { advance: (ms: number) => Promise<void> }, over: Record<string, unknown> = {}) {
  const ui = await $.ui.mount({ plugin: 'ctxr-live', surface: 'terminal', ...MAP(over) })
  await clock.advance(1)
  await ui.unmount()
}

test('the band yields while the map pane is docked and shown', SIDEBAR, async ($, on) => {
  const { clock, showPane } = await ready($, on)
  showPane()
  await seat($, clock)
  expect(await bandShowsMap($)).toBe(false)
})

test('the band draws while the map pane is a background tab', SIDEBAR, async ($, on) => {
  const { clock, showPane } = await ready($, on)
  showPane({ isShown: false })
  await seat($, clock)
  expect(await bandShowsMap($)).toBe(true)
})

test('the band draws while the map pane waits unplaced', SIDEBAR, async ($, on) => {
  const { showPane, state } = await ready($, on)
  showPane({ isPlaced: false })
  state.set('placement', 'dock')
  expect(await bandShowsMap($)).toBe(true)
})

test('the band draws when the pane sits inline', SIDEBAR, async ($, on) => {
  const { clock, showPane } = await ready($, on)
  showPane()
  await seat($, clock)
  await seat($, clock, { placement: 'inline' })
  expect(await bandShowsMap($)).toBe(true)
})

test('a stale docked placement without the pane draws the band', SIDEBAR, async ($, on) => {
  const { hidePanes, state } = await ready($, on)
  hidePanes()
  state.set('placement', 'dock')
  expect(await bandShowsMap($)).toBe(true)
})

test('bottom mode draws the band whatever the panes say', async ($, on) => {
  const { showPane, state } = await ready($, on)
  showPane()
  state.set('placement', 'dock')
  expect(await bandShowsMap($)).toBe(true)
})

test('refresh passes --height once the docked pane measures', SIDEBAR, async ($, on) => {
  const { clock, runs } = await ready($, on)
  expect(runs[0]).not.toContain('--height')
  await seat($, clock)
  await clock.advance(400)
  expect(runs.at(-1)!.slice(-2)).toEqual(['--height', '26'])
})

test('a pane too short for the map sends no --height', SIDEBAR, async ($, on) => {
  const { clock, runs } = await ready($, on)
  await seat($, clock, { scroll: { offset: 0, bodyRows: 4 } })
  await clock.advance(400)
  expect(runs.length).toBe(2)
  expect(runs.at(-1)).not.toContain('--height')
})
