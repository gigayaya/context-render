import type { Elements, RenderSurface } from 'claude-code'

import type { Role, Seg, Snapshot } from '../types'
import { MIN_BODY, timelineLine, withoutLabelLanes, withoutOccupancy } from './core'

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
        <Text dimColor wrap="truncate-end">{input.error ?? ''}</Text>
      </Box>
    )
  }
  const status = `${input.isRefreshing ? ' ↻' : ''}${input.error ? ' ⚠ stale' : ''}`
  const map = withoutLabelLanes(withoutOccupancy(s.map))
  return (
    <Box flexDirection="column">
      {map.map((segs, i) =>
        segRow(kit, i === 0 && status ? [...segs, { t: status, role: 'dim', no: null }] : segs, `m${i}`),
      )}
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
  const room = Math.max(1, rows - (error ? 3 : 2))
  return (
    <Box flexDirection="column">
      {error && <Text key="err" dimColor wrap="truncate-end">{error} (showing the last snapshot)</Text>}
      {s.timeline.slice(-room).map(row => (
        <Text key={`t${row.no}`} dimColor={row.confidence === 'heuristic' || row.sidechain} wrap="truncate-end">
          {timelineLine(row)}
        </Text>
      ))}
    </Box>
  )
}
