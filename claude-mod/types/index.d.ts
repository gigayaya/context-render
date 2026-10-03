export type Role = 'load' | 'action' | 'compaction' | 'occupancy' | 'dim' | 'bold'

export type Seg = { t: string; role: Role | null; no: number | null }

export type TimelineRow = {
  no: number
  ts: string | null
  kind: string
  component: string | null
  transition: string | null
  detail: string
  confidence: 'exact' | 'heuristic'
  est_tokens: number | null
  ctx_tokens: number | null
  sidechain: boolean
  tool?: string | null // action rows: the tool behind the action (Bash, Edit, ...)
}

export type Snapshot = {
  schema_version: number
  session_id: string
  cc_version: string | null
  width: number
  map: Seg[][]
  axis: Seg[] | null
  last: { load: number | null; action: number | null } // timeline `no` behind each arrow row's rightmost mark
  timeline: TimelineRow[]
  occupancy: { current: number | null; peak: number | null; window: number }
  warnings: string[]
  notice: string | null // set when ctxr ran without a manifest (not init'd)
}

declare module 'claude-code' {
  interface PluginState {
    'ctxr-live': { snapshot: Snapshot | null; error: string | null; isRefreshing: boolean }
  }
}
