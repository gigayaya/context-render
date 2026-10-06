import type { Role, Seg, Snapshot, TimelineRow } from '../types'

export const SCHEMA_VERSION = 4 // must match context_render/report/live.py LIVE_SCHEMA_VERSION
export const GUTTER = 10 // context_map.GUTTER
export const RIGHT = 32 // widest right-hand note: " occupancy · peak 999.9k/1M tok"
export const MIN_WIDTH = 20
export const MAX_WIDTH = 200
export const MIN_BODY = GUTTER + 2 + MIN_WIDTH + RIGHT // 64: below this the map cannot fit
export const TIMEOUT_MS = 5000
export const NAME_MAX = 15 // sidebar event names: code points before the cut
export const SIDEBAR_COLUMNS = 26 // bar 4 + gap + arrows 2 + gap + name 15 + " ~" + slack
export const SIDEBAR_CHROME = 4 // sidebar rows that are not events: header, legend, two borders
export const MAX_HEIGHT = 200 // ctxr live --height ceiling

export type Layout = 'bottom' | 'sidebar'
export type Placement = 'dock' | 'inline'

export function parseLayout(value: unknown): Layout {
  return value === 'sidebar' ? 'sidebar' : 'bottom'
}

export type RunOutcome =
  | { kind: 'ok'; snapshot: Snapshot }
  | { kind: 'error'; message: string; debug?: string }

export function innerWidth(bodyColumns: number): number {
  return Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, bodyColumns - GUTTER - 2 - RIGHT))
}

export function commandArgv(command: string): string[] {
  const argv = command.trim().split(/\s+/).filter(part => part.length > 0)
  return argv.length > 0 ? argv : ['ctxr']
}

export function liveArgv(command: string, sessionId: string, width: number, height?: number): string[] {
  const argv = [...commandArgv(command), 'live', sessionId, '--json', '--width', String(width)]
  return height === undefined ? argv : [...argv, '--height', String(height)]
}

function firstLine(text: string): string {
  return text.split('\n').map(line => line.trim()).find(line => line.length > 0) ?? ''
}

export function parseRun(exitCode: number, stdout: string, stderr: string): RunOutcome {
  if (exitCode === 3) {
    return { kind: 'error', message: `ctxr-live: ${firstLine(stderr).replace(/^Error:\s*/, '')}` }
  }
  if (exitCode !== 0) {
    return { kind: 'error', message: `ctxr-live: ctxr exited ${exitCode}`, debug: stderr }
  }
  let data: unknown
  try {
    data = JSON.parse(stdout)
  } catch {
    return { kind: 'error', message: 'ctxr-live: unreadable ctxr output', debug: stdout.slice(0, 2000) }
  }
  const version =
    typeof data === 'object' && data !== null
      ? (data as { schema_version?: unknown }).schema_version
      : undefined
  if (version !== SCHEMA_VERSION) {
    return {
      kind: 'error',
      message: `ctxr-live: snapshot schema ${String(version)} not supported — update ctxr or the mod`,
    }
  }
  return { kind: 'ok', snapshot: data as Snapshot }
}

export function spawnFailed(argv0: string, err: unknown): RunOutcome {
  return {
    kind: 'error',
    message: `ctxr-live: could not run ${argv0} — set the command in /config`,
    debug: String(err),
  }
}

// $.process.run rejects both when the command cannot start and when it is killed at the
// deadline; only the first is a /config problem
export function timedOut(err: unknown): RunOutcome {
  return {
    kind: 'error',
    message: `ctxr-live: ctxr timed out after ${TIMEOUT_MS / 1000}s`,
    debug: String(err),
  }
}

export function shortTokens(n: number): string {
  const one = (x: number) => String(Number(x.toFixed(1)))
  if (n >= 1_000_000) return `${one(n / 1_000_000)}M`
  if (n >= 1000) return `${one(n / 1000)}k`
  return String(n)
}

export function rowTag(row: TimelineRow): string {
  if (row.kind === 'compaction') return '⟐'
  if (row.kind === 'session_start') return 'start'
  if (row.kind === 'session_end') return 'end'
  if (row.transition === 'loaded') return 'L'
  if (row.transition === 'invoked') return 'I'
  if (row.kind === 'file_read') return 'read'
  if (row.kind === 'action') return 'act'
  return row.kind
}

export function hhmmss(ts: string | null): string {
  if (!ts) return '--:--:--'
  const date = new Date(ts)
  return Number.isNaN(date.getTime()) ? '--:--:--' : date.toTimeString().slice(0, 8)
}

export function timelineLine(row: TimelineRow): string {
  const ctx = row.ctx_tokens === null ? '·' : shortTokens(row.ctx_tokens)
  const mark = row.confidence === 'heuristic' ? ' ~' : ''
  const what = row.component ? `${row.component} ${row.detail}` : row.detail
  return `${String(row.no).padStart(3)} ${hhmmss(row.ts)} ${ctx.padStart(6)} ─ ${rowTag(row)}${mark} ${what}`
}

// the band leaves out the occupancy bar: the `window` row plus the box borders around it
export function withoutOccupancy(map: Seg[][]): Seg[][] {
  const at = map.findIndex(segs => segs[0]?.t.trim() === 'window')
  return at < 0 ? map : [...map.slice(0, Math.max(0, at - 1)), ...map.slice(at + 2)]
}

// label lanes are the rows carrying timeline numbers; the band draws the arrow rows without them
export function withoutLabelLanes(map: Seg[][]): Seg[][] {
  return map.filter(segs => !segs.some(s => s.no !== null))
}

// the name the band writes after an arrow: component id, tool + target for actions, else detail
export function eventName(row: TimelineRow): string {
  const mark = row.confidence === 'heuristic' ? ' ~' : ''
  if (row.component) return `${row.component}${mark}`
  if (row.kind === 'action' && row.tool) return `${row.tool} ${row.detail}${mark}`
  return `${row.detail}${mark}`
}

const ASSIGNMENT = /^[A-Za-z_][A-Za-z0-9_]*=/

// an action's target without the tool: leading VAR=... dropped, paths cut to their basename
function actionTarget(detail: string): string {
  const words = detail.split(/\s+/).filter(word => word.length > 0)
  while (words.length > 0 && ASSIGNMENT.test(words[0]!)) words.shift()
  return words
    .map(word => (word.includes('/') && !word.endsWith('/') ? word.slice(word.lastIndexOf('/') + 1) : word))
    .join(' ')
}

// the sidebar's per-row name: component without its kind, action target, else the detail;
// cut to NAME_MAX code points, then ` ~` for heuristic rows
export function sidebarName(row: TimelineRow): string {
  const mark = row.confidence === 'heuristic' ? ' ~' : ''
  let base: string
  if (row.component) base = row.component.slice(row.component.indexOf(':') + 1)
  else if (row.kind === 'action') base = actionTarget(row.detail) || eventName({ ...row, confidence: 'exact' })
  else base = row.detail
  return Array.from(base).slice(0, NAME_MAX).join('') + mark
}

export function nameRole(row: TimelineRow): Role {
  if (row.kind === 'compaction') return 'compaction'
  if (row.kind === 'file_read' || row.transition === 'loaded') return 'load'
  return 'action'
}

// event rows the docked pane fits; null when it fits none (no --height is sent)
export function sidebarHeight(bodyRows: number | null): number | null {
  if (bodyRows === null) return null
  const height = Math.min(MAX_HEIGHT, bodyRows - SIDEBAR_CHROME)
  return height >= 1 ? height : null
}

// the sidebar draws the map only while its pane is the shown, placed, docked one; else the band
export function isSidebarActive(
  layout: Layout,
  pane: { isShown: boolean; isPlaced: boolean } | undefined,
  placement: Placement | null,
): boolean {
  return layout === 'sidebar' && pane?.isShown === true && pane.isPlaced && placement === 'dock'
}

const ARROW_ROWS: Record<string, { side: 'load' | 'action'; role: Role }> = {
  loads: { side: 'load', role: 'load' },
  actions: { side: 'action', role: 'action' },
}

// each arrow row gets the name of the event behind its rightmost mark (snapshot `last`)
export function withLastNames(map: Seg[][], s: Snapshot): Seg[][] {
  const byNo = new Map(s.timeline.map(row => [row.no, row]))
  return map.map(segs => {
    const arrow = ARROW_ROWS[segs[0]?.t.trim() ?? '']
    const no = arrow ? s.last?.[arrow.side] : null
    const row = no == null ? undefined : byNo.get(no)
    return row ? [...segs, { t: ` ${eventName(row)}`, role: arrow!.role, no: null }] : segs
  })
}
