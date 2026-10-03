import { expect, mock, test } from 'claude-code/testing'
import type { On } from 'claude-code'

import { engineDraws, sessionStart } from './engine'

const seg = (t: string, role: string | null = null, no: number | null = null) => ({ t, role, no })
const ROW = (no: number, over: Record<string, unknown>) => ({
  no, ts: null, kind: 'action', component: null, transition: null, detail: 'x',
  confidence: 'exact', est_tokens: 0, ctx_tokens: null, sidechain: false, ...over,
})
const SNAP = {
  schema_version: 2, session_id: 'sess-1', cc_version: '2.1.288', width: 56,
  map: [[seg('  '), seg('context window map', 'bold')], [seg('           '), seg('2', 'load', 2)]],
  axis: null,
  timeline: [
    ROW(1, { kind: 'session_start', detail: 'cc 2.1.288' }),
    ROW(2, { kind: 'claude_md', transition: 'loaded', component: 'claude-md:root', detail: 'root CLAUDE.md', est_tokens: 739 }),
    ROW(3, { kind: 'file_read', detail: '[subagent:x] Read b.md', sidechain: true }),
  ],
  occupancy: { current: null, peak: null, window: 200000 },
  warnings: [] as string[],
  notice: null as string | null,
}

async function ready($: any, on: On) {
  const clock = mock.clock(on)
  const opened: unknown[] = []
  sessionStart(on)
  engineDraws(on)
  on('process.run', () => ({ value: { exitCode: 0, stdout: JSON.stringify(SNAP), stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }))
  on('session.id', () => ({ value: 'sess-1' }))
  on('ui.open', ($$, e) => { opened.push(e); return { value: { isPlaced: true as const } } })
  await $.session.start({ cwd: '/repo', surface: 'terminal', isInteractive: true })
  await clock.advance(400)
  return { opened }
}

const PANE_PROPS = {
  title: 'context timeline', isFocused: false, bodyColumns: 100, placement: 'dock' as const,
  scroll: { offset: 0, bodyRows: 30 }, view: {},
}

test('/ctx opens the pane', async ($, on) => {
  const { opened } = await ready($, on)
  const out = await $.command.run({
    command: 'ctx', args: '', origin: { kind: 'composer' },
    presentation: { isFullscreen: false, columns: 100 },
  })
  expect(opened).toEqual([expect.objectContaining({ id: 'ctxr-live' })])
  expect(out.text).toMatch(/context timeline/)
})

test('the pane lists every timeline row, sidechain included', async ($, on) => {
  await ready($, on)
  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ plugin: 'ctxr-live', surface, component: 'Pane', requestId: 'ctxr-live', props: PANE_PROPS })
    expect(await ui.find({ type: 'Text', text: /^ {2}1 .*start cc 2\.1\.288$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^ {2}2 .*─ L claude-md:root root CLAUDE\.md$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /\[subagent:x\] Read b\.md/ })).toBeDefined()
    await ui.unmount()
  }
})

test('the pane leads with the notice', async ($, on) => {
  const clock = mock.clock(on)
  sessionStart(on)
  engineDraws(on)
  const noticed = { ...SNAP, notice: "not init'd: run ctxr init" }
  on('process.run', () => ({ value: { exitCode: 0, stdout: JSON.stringify(noticed), stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }))
  on('session.id', () => ({ value: 'sess-1' }))
  await $.session.start({ cwd: '/repo', surface: 'terminal', isInteractive: true })
  await clock.advance(400)
  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ plugin: 'ctxr-live', surface, component: 'Pane', requestId: 'ctxr-live', props: PANE_PROPS })
    expect(await ui.find({ type: 'Text', text: "ctxr-live: not init'd: run ctxr init" })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /root CLAUDE\.md$/ })).toBeDefined()
    await ui.unmount()
  }
})
