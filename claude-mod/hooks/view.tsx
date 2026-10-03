import type { Elements, RenderSurface } from 'claude-code'

import type { Role, Seg, Snapshot, TimelineRow } from '../types'
import { MIN_BODY, planBand, rowTag, shortTokens, timelineLine } from './core'

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
  maxRows: number
  bodyColumns: number
  hover: boolean
}

// hover is scoped to the nearest keyed Box, which must stay visible: the label's own Box
// carries the key (`card-<no>` when the row is known) and the card hides inside it
function labelWithCard(kit: Kit, seg: Seg, row: TimelineRow | undefined, key: string) {
  const { Box, Text } = kit
  return (
    <Box key={row ? `card-${row.no}` : key}>
      <Text {...roleProps(seg.role)}>{seg.t}</Text>
      {row && (
        <Box position="absolute" top={1} left={0} display="none"
             hover={{ display: 'flex' }} flexDirection="column" borderStyle="round" paddingX={1}>
          <Text bold>#{row.no} {rowTag(row)} {row.component ?? '—'}</Text>
          <Text dimColor>
            {row.confidence} · {row.est_tokens ? `${shortTokens(row.est_tokens)} tok` : 'size unknown'}
          </Text>
          <Text wrap="truncate-end">{row.detail}</Text>
        </Box>
      )}
    </Box>
  )
}

// one map row. Plain: nested Texts in a truncating Text (an over-long row is cut at the
// band edge). With hover (fullscreen terminal only) a row holding labels becomes a Box row
// so each label can scope its own absolute card.
export function segRow(kit: Kit, segs: Seg[], key: string, hover: boolean, byNo: Map<number, TimelineRow>) {
  const { Box, Text } = kit
  if (hover && segs.some(s => s.no !== null)) {
    return (
      <Box key={key} flexDirection="row">
        {segs.map((s, i) =>
          s.no !== null
            ? labelWithCard(kit, s, byNo.get(s.no), `${key}-${i}`)
            : <Text key={`${key}-${i}`} {...roleProps(s.role)}>{s.t}</Text>,
        )}
      </Box>
    )
  }
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
  const warnings = s.warnings.length > 0 ? s.warnings.join(' · ') : null
  const plan = planBand(s, input.maxRows, warnings !== null)
  const byNo = new Map(s.timeline.map(row => [row.no, row] as const))
  return (
    <Box flexDirection="column">
      {plan.map.map((segs, i) =>
        segRow(kit, i === 0 && status ? [...segs, { t: status, role: 'dim', no: null }] : segs, `m${i}`, input.hover, byNo),
      )}
      {plan.axis && segRow(kit, plan.axis, 'axis', false, byNo)}
      {warnings && <Text key="warn" dimColor wrap="truncate-end">{warnings}</Text>}
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
