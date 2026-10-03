import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { Snapshot } from '../types'
import { innerWidth, liveArgv, parseRun, spawnFailed, timedOut, TIMEOUT_MS } from './core'
import type { RunOutcome } from './core'
import { Scheduler } from './scheduler'
import { bandTree, paneTree } from './view'

export const PANE = 'ctxr-live'
export const snapshot = atom({ plugin: 'ctxr-live', key: 'snapshot' } as const, null as Snapshot | null)
export const error = atom({ plugin: 'ctxr-live', key: 'error' } as const, null as string | null)
export const isRefreshing = atom({ plugin: 'ctxr-live', key: 'isRefreshing' } as const, false)

// where and how to run ctxr; read at refresh time (the band measures columns as it draws)
type Target = { command: string; cwd: () => string; columns: () => number }

// a hooks module passes $ only to functions declared at its top level
async function refresh($: EngineInterface, target: Target): Promise<void> {
  await update($, isRefreshing, () => true)
  try {
    const sessionId = await $.session.id()
    const argv = liveArgv(target.command, sessionId, innerWidth(target.columns()))
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
  let cwd = ''
  let columns = 100 // until the band first measures; render updates it
  let scheduler: Scheduler | null = null
  const target: Target = { command, cwd: () => cwd, columns: () => columns }

  on('session.start', async ($, e, next) => {
    if (!e.isInteractive) return next(e)
    cwd = e.cwd
    // state outlives a reload, module variables do not: a reload mid-refresh left it true
    await update($, isRefreshing, () => false)
    await $.command.register({ name: 'ctx', description: 'Show the context timeline (ctxr-live) in a pane' })
    scheduler = new Scheduler({ after: (ms, fn) => $.clock.after(ms, fn), run: () => refresh($, target) }, debounceMs)
    scheduler.poke()
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
    if (e.props.bodyColumns !== columns) {
      columns = e.props.bodyColumns // module variable: only feeds --width, no drawing reads it
      scheduler?.poke()
    }
    const snap = await read($, snapshot)
    const err = await read($, error)
    if ((!snap || snap.map.length === 0) && !err) return next(e)
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
}
