import type { Elements, RenderSurface } from 'claude-code'

import type { Role, Seg, Snapshot } from '../types'
import type { Placement } from './core'
import { MIN_BODY, nameRole, sidebarName, timelineLine, withLastNames, withoutLabelLanes, withoutOccupancy } from './core'

export type Kit = Pick<Elements[RenderSurface], 'Box' | 'Text'>

const ROLE_PROPS: Record<Role, { color?: string; dimColor?: boolean; bold?: boolean }> = {
  load: { color: 'cyan' },
  action: { color: 'yellow' },
  compaction: { color: 'magenta' },
  occupancy: { color: 'green' },
  dim: { dimColor: true },
  bold: { bold: true },
}

export function roleProps(role: Role | null) {
  return role ? ROLE_PROPS[role] : {}
}

export type BandInput = {
  snapshot: Snapshot | null
  error: string | null
  isRefreshing: boolean
  bodyColumns: number
}

// one map row: nested Texts in a truncating Text (an over-long row is cut at the band edge)
export function segRow(kit: Kit, segs: Seg[], key: string) {
  const { Text } = kit
  return (
    <Text key={key} wrap="truncate-end">
      {segs.map((s, i) => <Text key={`${key}-${i}`} {...roleProps(s.role)}>{s.t}</Text>)}
    </Text>
  )
}

// the snapshot's notice (e.g. not init'd), worded like the mod's other one-line messages
function noticeLine(s: Snapshot | null): string | null {
  return s?.notice ? `ctxr-live: ${s.notice}` : null
}

export function bandTree(kit: Kit, input: BandInput) {
  const { Box, Text } = kit
  const s = input.snapshot
  if (input.bodyColumns < MIN_BODY) {
    return (
      <Box>
        <Text dimColor wrap="truncate-end">ctxr-live: terminal too narrow for the map ({MIN_BODY}+ cols) — /ctx shows the timeline</Text>
      </Box>
    )
  }
  if (!s || s.map.length === 0) {
    return (
      <Box>
        <Text dimColor wrap="truncate-end">{input.error ?? noticeLine(s) ?? ''}</Text>
      </Box>
    )
  }
  const status = `${input.isRefreshing ? ' ↻' : ''}${input.error ? ' ⚠ stale' : ''}`
  const map = withLastNames(withoutLabelLanes(withoutOccupancy(s.map)), s)
  return (
    <Box flexDirection="column">
      {map.map((segs, i) =>
        segRow(kit, i === 0 && status ? [...segs, { t: status, role: 'dim', no: null }] : segs, `m${i}`),
      )}
      {s.notice && <Text key="notice" dimColor wrap="truncate-end">{noticeLine(s)}</Text>}
    </Box>
  )
}

export function paneTree(kit: Kit, s: Snapshot | null, error: string | null, rows: number) {
  const { Box, Text } = kit
  if (!s) {
    return (
      <Box>
        <Text dimColor>{error ?? 'ctxr-live: no snapshot yet'}</Text>
      </Box>
    )
  }
  const room = Math.max(1, rows - 2 - (error ? 1 : 0) - (s.notice ? 1 : 0))
  return (
    <Box flexDirection="column">
      {error && <Text key="err" dimColor wrap="truncate-end">{error} (showing the last snapshot)</Text>}
      {s.notice && <Text key="notice" dimColor wrap="truncate-end">{noticeLine(s)}</Text>}
      {s.timeline.slice(-room).map(row => (
        <Text key={`t${row.no}`} dimColor={row.confidence === 'heuristic' || row.sidechain} wrap="truncate-end">
          {timelineLine(row)}
        </Text>
      ))}
    </Box>
  )
}

export const SIDEBAR_INLINE_HINT = 'ctxr-live: sidebar needs fullscreen and 110+ cols — the map is in the band'

export type SidebarInput = {
  snapshot: Snapshot | null
  error: string | null
  isRefreshing: boolean
  placement: Placement
}

// the docked map: vertical rows from ctxr, each event row followed by its cleaned name
export function sidebarTree(kit: Kit, input: SidebarInput) {
  const { Box, Text } = kit
  if (input.placement === 'inline') {
    return (
      <Box>
        <Text dimColor wrap="truncate-end">{SIDEBAR_INLINE_HINT}</Text>
      </Box>
    )
  }
  const s = input.snapshot
  const v = s?.vertical
  if (!s || !v || v.rows.length === 0) {
    return (
      <Box>
        <Text dimColor wrap="truncate-end">{input.error ?? noticeLine(s) ?? (s ? 'ctxr-live: no map yet' : 'ctxr-live: no snapshot yet')}</Text>
      </Box>
    )
  }
  const byNo = new Map(s.timeline.map(row => [row.no, row]))
  const status = `${input.isRefreshing ? ' ↻' : ''}${input.error ? ' ⚠ stale' : ''}`
  const lines: Seg[][] = v.rows.map((segs, i) => {
    const no = v.row_no[i]
    const row = no == null ? undefined : byNo.get(no)
    if (row) {
      const role: Role = row.confidence === 'heuristic' || row.sidechain ? 'dim' : nameRole(row)
      return [...segs, { t: ` ${sidebarName(row)}`, role, no: null }]
    }
    return i === 0 && status ? [...segs, { t: status, role: 'dim', no: null }] : segs
  })
  return (
    <Box flexDirection="column">
      {lines.slice(0, 2).map((segs, i) => segRow(kit, segs, `v${i}`))}
      {input.error && <Text key="err" dimColor wrap="truncate-end">{input.error}</Text>}
      {s.notice && <Text key="notice" dimColor wrap="truncate-end">{noticeLine(s)}</Text>}
      {lines.slice(2).map((segs, i) => segRow(kit, segs, `v${i + 2}`))}
    </Box>
  )
}
