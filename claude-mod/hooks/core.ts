import type { Seg, Snapshot, TimelineRow } from '../types'

export const SCHEMA_VERSION = 1 // must match context_render/report/live.py LIVE_SCHEMA_VERSION
export const GUTTER = 10 // context_map.GUTTER
export const RIGHT = 32 // widest right-hand note: " occupancy · peak 999.9k/1M tok"
export const MIN_WIDTH = 20
export const MAX_WIDTH = 200
export const MIN_BODY = GUTTER + 2 + MIN_WIDTH + RIGHT // 64: below this the map cannot fit
export const TIMEOUT_MS = 5000

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

export function liveArgv(command: string, sessionId: string, width: number): string[] {
  return [...commandArgv(command), 'live', sessionId, '--json', '--width', String(width)]
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

// same classification as context_map_parts: loads above the bar, actions below
export function eventSide(row: TimelineRow): 'load' | 'action' | null {
  if (row.kind === 'compaction') return null
  if (row.kind === 'file_read' || row.transition === 'loaded') return 'load'
  if (row.kind === 'action' || row.transition === 'invoked') return 'action'
  return null
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

export function recentEvents(timeline: TimelineRow[], n = 3): TimelineRow[] {
  return timeline.filter(row => !row.sidechain && eventSide(row) !== null).slice(-n)
}

export function eventLine(row: TimelineRow): string {
  const arrow = eventSide(row) === 'load' ? '▼' : '▲'
  const tokens = row.est_tokens ? `  ${shortTokens(row.est_tokens)} tok` : ''
  return `#${row.no} ${arrow} ${rowTag(row)} ${row.component ?? row.detail}  ${row.confidence}${tokens}`
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

export type BandPlan = { map: Seg[][]; axis: Seg[] | null; recent: TimelineRow[] }

// degrade order when the band has fewer rows than the full drawing: recent lines, then the axis
export function planBand(s: Snapshot, maxRows: number, hasWarnings: boolean): BandPlan {
  const recent = recentEvents(s.timeline)
  const fixed = s.map.length + (hasWarnings ? 1 : 0)
  const axisRows = s.axis ? 1 : 0
  if (fixed + axisRows + recent.length <= maxRows) return { map: s.map, axis: s.axis, recent }
  if (fixed + axisRows <= maxRows) return { map: s.map, axis: s.axis, recent: [] }
  return { map: s.map, axis: null, recent: [] }
}
