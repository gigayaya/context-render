import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { Snapshot } from '../types'
import { innerWidth, isSidebarActive, liveArgv, parseLayout, parseRun, sidebarHeight, spawnFailed, timedOut, SIDEBAR_COLUMNS, TIMEOUT_MS } from './core'
import type { RunOutcome } from './core'
import { Scheduler } from './scheduler'
import { bandTree, paneTree, sidebarTree } from './view'

export const PANE = 'ctxr-live'
export const snapshot = atom({ plugin: 'ctxr-live', key: 'snapshot' } as const, null as Snapshot | null)
export const error = atom({ plugin: 'ctxr-live', key: 'error' } as const, null as string | null)
export const isRefreshing = atom({ plugin: 'ctxr-live', key: 'isRefreshing' } as const, false)
export const MAP_PANE = 'ctxr-map'
const MAP_TITLE = 'context map'
export const placement = atom({ plugin: 'ctxr-live', key: 'placement' } as const, null as 'dock' | 'inline' | null)

// a hooks module passes $ only to functions declared at its top level
async function openMap($: EngineInterface): Promise<void> {
  await $.ui.open({ id: MAP_PANE, title: MAP_TITLE, columns: SIDEBAR_COLUMNS })
}

// where and how to run ctxr; read at refresh time (the band and the map pane measure as they draw)
type Target = { command: string; cwd: () => string; columns: () => number; height: () => number | null }

// a hooks module passes $ only to functions declared at its top level
async function refresh($: EngineInterface, target: Target): Promise<void> {
  await update($, isRefreshing, () => true)
  try {
    const sessionId = await $.session.id()
    const height = target.height()
    const argv = liveArgv(target.command, sessionId, innerWidth(target.columns()), height ?? undefined)
    let outcome: RunOutcome
    const startedAt = await $.clock.now()
    try {
      const ran = await $.process.run(argv, { cwd: target.cwd(), timeoutMs: TIMEOUT_MS })
      outcome = parseRun(ran.exitCode, ran.stdout, ran.stderr)
    } catch (err) {
      const isTimeout = (await $.clock.now()) - startedAt >= TIMEOUT_MS
      outcome = isTimeout ? timedOut(err) : spawnFailed(argv[0] ?? 'ctxr', err)
    }
    if (outcome.kind === 'ok') {
      const fresh = outcome.snapshot
      await update($, snapshot, () => fresh)
      await update($, error, () => null)
    } else {
      // another session's map (after /clear or resume) must not linger as "stale"
      await update($, snapshot, prev => (prev && !prev.session_id.startsWith(sessionId) && !sessionId.startsWith(prev.session_id) ? null : prev))
      const message = outcome.message
      await update($, error, () => message)
      if (outcome.debug) $.ui.log(outcome.debug, { to: 'debug' })
    }
  } finally {
    await update($, isRefreshing, () => false)
  }
}

export const register: Register = (on, options) => {
  const command = typeof options.command === 'string' ? options.command : 'ctxr'
  const debounceMs = typeof options.debounceMs === 'number' ? options.debounceMs : 400
  const layout = parseLayout(options.layout)
  let cwd = ''
  let columns = 100 // until the band first measures; render updates it
  let scheduler: Scheduler | null = null
  let sidebarRows: number | null = null // until the docked map pane first measures; only feeds --height
  const target: Target = {
    command,
    cwd: () => cwd,
    columns: () => columns,
    height: () => (layout === 'sidebar' ? sidebarHeight(sidebarRows) : null),
  }

  on('session.start', async ($, e, next) => {
    if (!e.isInteractive) return next(e)
    cwd = e.cwd
    // state outlives a reload, module variables do not: a reload mid-refresh left it true
    await update($, isRefreshing, () => false)
    await $.command.register({ name: 'ctx', description: 'Show the context timeline (ctxr-live) in a pane' })
    if (layout === 'sidebar') {
      await $.command.register({ name: 'ctx-map', description: 'Open the sidebar context map (ctxr-live)' })
    }
    scheduler = new Scheduler({ after: (ms, fn) => $.clock.after(ms, fn), run: () => refresh($, target) }, debounceMs)
    scheduler.poke()
    // unasked: placed from 144 columns (110 once the person opened it); the band covers until then
    if (layout === 'sidebar') await openMap($)
    return next(e)
  })

  on('tool.call', async ($, e, next) => {
    const ran = await next(e)
    scheduler?.poke()
    return ran
  })

  on('turn.complete', async ($, e, next) => {
    const ran = await next(e)
    scheduler?.poke()
    return ran
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.props.hasSurvey) return next(e)
    if (layout === 'sidebar') {
      // the docked map pane has it; any other seat (tab, unplaced, inline, closed) falls back here
      const pane = (await $.ui.panes()).find(p => p.id === MAP_PANE)
      if (isSidebarActive(layout, pane, await read($, placement))) return next(e)
    }
    if (e.props.bodyColumns !== columns) {
      columns = e.props.bodyColumns // module variable: only feeds --width, no drawing reads it
      scheduler?.poke()
    }
    const snap = await read($, snapshot)
    const err = await read($, error)
    if ((!snap || (snap.map.length === 0 && !snap.notice)) && !err) return next(e)
    return bandTree($.ui.resolve(e), {
      snapshot: snap,
      error: err,
      isRefreshing: await read($, isRefreshing),
      bodyColumns: e.props.bodyColumns,
    })
  })

  on('command.run', { command: 'ctx' }, async $ => {
    await $.ui.open({ id: PANE, title: 'context timeline' })
    return { text: 'ctxr-live: context timeline opened.' }
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) =>
    paneTree($.ui.resolve(e), await read($, snapshot), await read($, error), e.viewport?.rows ?? 24),
  )

  if (layout === 'sidebar') {
    on('command.run', { command: 'ctx-map' }, async $ => {
      await openMap($)
      return { text: 'ctxr-live: context map opened.' }
    })

    on('ui.render', { component: 'Pane', requestId: MAP_PANE }, async ($, e) => {
      const seated = e.props.placement
      if ((await read($, placement)) !== seated) $.clock.after(0, () => update($, placement, () => seated))
      if (e.props.scroll.bodyRows !== sidebarRows) {
        sidebarRows = e.props.scroll.bodyRows
        scheduler?.poke()
      }
      return sidebarTree($.ui.resolve(e), {
        snapshot: await read($, snapshot),
        error: await read($, error),
        isRefreshing: await read($, isRefreshing),
        placement: seated,
      })
    })

    on('ui.close', { id: MAP_PANE }, async ($, e, next) => {
      const ran = await next(e)
      await update($, placement, () => null) // the band takes the map back at once
      return ran
    })
  }
}
