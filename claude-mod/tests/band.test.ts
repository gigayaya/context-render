import { expect, mock, test } from 'claude-code/testing'
import type { On } from 'claude-code'

import { engineDraws, sessionStart } from './engine'

const seg = (t: string, role: string | null = null, no: number | null = null) => ({ t, role, no })
const ROW = (no: number, over: Record<string, unknown>) => ({
  no, ts: null, kind: 'action', component: null, transition: null, detail: 'x',
  confidence: 'exact', est_tokens: 0, ctx_tokens: null, sidechain: false, ...over,
})
const SNAP = {
  schema_version: 3, session_id: 'sess-1', cc_version: '2.1.288', width: 56,
  map: [
    [seg('  '), seg('context window map', 'bold')],
    [seg('           '), seg('2', 'load', 2)],
    [seg('  loads    '), seg('▼', 'load')],
  ],
  axis: [seg('          '), seg('14:00:00            14:09:00', 'dim')],
  last: { load: 2, action: null } as { load: number | null; action: number | null },
  timeline: [
    ROW(1, { kind: 'session_start' }),
    ROW(2, { kind: 'claude_md', transition: 'loaded', component: 'claude-md:root', est_tokens: 739 }),
    ROW(3, { kind: 'action', detail: 'Bash pytest', confidence: 'heuristic' }),
  ],
  occupancy: { current: null, peak: null, window: 200000 },
  warnings: [] as string[],
  notice: null as string | null,
}
const SURFACES = ['terminal', 'desktop'] as const
const BAND = (over: Record<string, unknown> = {}) => {
  const props = { hasSurvey: false, isWorking: false, maxRows: 30, bodyColumns: 100, ...over }
  return {
    component: 'AbovePrompt' as const,
    props: { ...props, scroll: { offset: 0, bodyRows: props.maxRows as number }, view: {} },
  }
}
type Reply = { exitCode: number; stdout: string; stderr: string }
const OK = (): Reply => ({ exitCode: 0, stdout: JSON.stringify(SNAP), stderr: '' })

async function withSnapshot($: any, on: On, reply: () => Reply = OK) {
  const clock = mock.clock(on)
  const runs: string[][] = []
  sessionStart(on)
  engineDraws(on)
  on('tool.call', () => ({ result: 'ok', text: 'ok' }))
  on('process.run', ($$, e) => {
    runs.push([...e.argv])
    return { value: { ...reply(), isStdoutTruncated: false, isStderrTruncated: false } }
  })
  on('session.id', () => ({ value: 'sess-1' }))
  on('ui.log', () => ({ value: undefined }))
  await $.session.start({ cwd: '/repo', surface: 'terminal', isInteractive: true })
  await clock.advance(400)
  return { clock, runs }
}

test('draws the map only: no axis, no recent-event lines', async ($, on) => {
  await withSnapshot($, on)
  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ plugin: 'ctxr-live', surface, ...BAND() })
    expect(await ui.find({ type: 'Text', text: /context window map/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /14:00:00/ })).toBeUndefined()
    expect(await ui.find({ type: 'Text', text: /#2 ▼/ })).toBeUndefined()
    expect(await ui.find({ type: 'Text', text: /#3 ▲/ })).toBeUndefined()
    await ui.unmount()
  }
})

test('draws the arrow rows without their number lanes', async ($, on) => {
  const laned = {
    ...SNAP,
    map: [
      [seg('  '), seg('context window map', 'bold')],
      [seg('           '), seg('2', 'load', 2)],
      [seg('           '), seg('1', 'load', 1)],
      [seg('  loads    '), seg('▼', 'load')],
      [seg('  actions  '), seg('▲', 'action')],
      [seg('           '), seg('3', 'action', 3)],
      [seg('           '), seg('4', 'action', 4)],
    ],
  }
  await withSnapshot($, on, () => ({ exitCode: 0, stdout: JSON.stringify(laned), stderr: '' }))
  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ plugin: 'ctxr-live', surface, ...BAND() })
    expect(await ui.find({ type: 'Text', text: '▼' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: '▲' })).toBeDefined()
    for (const no of ['1', '2', '3', '4']) expect(await ui.find({ type: 'Text', text: no })).toBeUndefined()
    await ui.unmount()
  }
})

test('names the latest event after the last arrow of each row', async ($, on) => {
  const named = {
    ...SNAP,
    map: [
      [seg('  '), seg('context window map', 'bold')],
      [seg('  loads    '), seg('▼ ▼', 'load')],
      [seg('  actions  '), seg('▲', 'action')],
    ],
    last: { load: 2, action: 3 },
  }
  await withSnapshot($, on, () => ({ exitCode: 0, stdout: JSON.stringify(named), stderr: '' }))
  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ plugin: 'ctxr-live', surface, ...BAND() })
    expect(await ui.find({ type: 'Text', text: ' claude-md:root' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: ' Bash pytest ~' })).toBeDefined()
    await ui.unmount()
  }
})

test('snapshot warnings are not drawn', async ($, on) => {
  const warned = { ...SNAP, warnings: ['⚠ parse degraded: unknown events [pr-link×1]'] }
  await withSnapshot($, on, () => ({ exitCode: 0, stdout: JSON.stringify(warned), stderr: '' }))
  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ plugin: 'ctxr-live', surface, ...BAND() })
    expect(await ui.find({ type: 'Text', text: /context window map/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /parse degraded/ })).toBeUndefined()
    await ui.unmount()
  }
})

test('the notice is one dim line under the map', async ($, on) => {
  const noticed = { ...SNAP, notice: "not init'd: run ctxr init" }
  await withSnapshot($, on, () => ({ exitCode: 0, stdout: JSON.stringify(noticed), stderr: '' }))
  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ plugin: 'ctxr-live', surface, ...BAND() })
    expect(await ui.find({ type: 'Text', text: /context window map/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: "ctxr-live: not init'd: run ctxr init" })).toBeDefined()
    await ui.unmount()
  }
})

test('the notice stands alone while the map is empty', async ($, on) => {
  const noticed = { ...SNAP, map: [], axis: null, notice: "not init'd: run ctxr init" }
  await withSnapshot($, on, () => ({ exitCode: 0, stdout: JSON.stringify(noticed), stderr: '' }))
  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ plugin: 'ctxr-live', surface, ...BAND() })
    expect(await ui.find({ type: 'Text', text: "ctxr-live: not init'd: run ctxr init" })).toBeDefined()
    await ui.unmount()
  }
})

test('yields with no snapshot and no error', async ($, on) => {
  mock.clock(on)
  engineDraws(on)
  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ plugin: 'ctxr-live', surface, ...BAND() })
    expect(await ui.find({ type: 'Text', text: /context window map/ })).toBeUndefined()
    await ui.unmount()
  }
})

test('yields when the map is empty', async ($, on) => {
  await withSnapshot($, on, () => ({ exitCode: 0, stdout: JSON.stringify({ ...SNAP, map: [], axis: null }), stderr: '' }))
  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ plugin: 'ctxr-live', surface, ...BAND() })
    expect(await ui.find({ type: 'Text', text: /#2/ })).toBeUndefined()
    await ui.unmount()
  }
})

test('yields to a survey', async ($, on) => {
  await withSnapshot($, on)
  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ plugin: 'ctxr-live', surface, ...BAND({ hasSurvey: true }) })
    expect(await ui.find({ type: 'Text', text: /context window map/ })).toBeUndefined()
    await ui.unmount()
  }
})

test('an error without a snapshot is one dim line', async ($, on) => {
  await withSnapshot($, on, () => ({ exitCode: 3, stdout: '', stderr: 'Error: manifest not found; run ctxr init first' }))
  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ plugin: 'ctxr-live', surface, ...BAND() })
    expect(await ui.find({ type: 'Text', text: 'ctxr-live: manifest not found; run ctxr init first' })).toBeDefined()
    await ui.unmount()
  }
})

test('a failed refresh keeps the map and marks it stale', async ($, on) => {
  let fail = false
  const { clock } = await withSnapshot($, on, () => (fail ? { exitCode: 1, stdout: '', stderr: 'boom' } : OK()))
  fail = true
  await $.tool.call({ tool: 'Read', file_path: '/repo/a.md' })
  await clock.advance(400)
  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ plugin: 'ctxr-live', surface, ...BAND() })
    expect(await ui.find({ type: 'Text', text: /context window map/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /⚠ stale/ })).toBeDefined()
    await ui.unmount()
  }
})

test('narrow band shows a one-line notice', async ($, on) => {
  await withSnapshot($, on)
  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ plugin: 'ctxr-live', surface, ...BAND({ bodyColumns: 50 }) })
    expect(await ui.find({ type: 'Text', text: /too narrow.*\/ctx/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /context window map/ })).toBeUndefined()
    await ui.unmount()
  }
})

test('a width change refreshes with the new --width', async ($, on) => {
  const { clock, runs } = await withSnapshot($, on)
  const ui = await $.ui.mount({ plugin: 'ctxr-live', surface: 'terminal', ...BAND({ bodyColumns: 140 }) })
  await clock.advance(400)
  expect(runs.at(-1)!.slice(-2)).toEqual(['--width', '96'])
  await ui.unmount()
})
