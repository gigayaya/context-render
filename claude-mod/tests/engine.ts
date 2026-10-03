import type { On, RenderElement } from 'claude-code'

// The test `$` has no `state` noun, so the bottom hooks answer `$.state` from memory
// (compare-and-set by version, as the host does) and the test reads what was written.
// A hook on a call of `$` (state, process, session.id ...) answers `{ value: <result> }`.
export function memState(on: On) {
  const cells = new Map<string, { value: unknown; version: number }>()
  const name = (e: { plugin: string; key: string; id?: string }) =>
    `${e.plugin}/${e.key}${e.id ? `/${e.id}` : ''}`
  on('state.get', ($, e) => {
    const cell = cells.get(name(e))
    return { value: { value: cell?.value, version: cell?.version ?? 0 } }
  })
  on('state.set', ($, e) => {
    const cell = cells.get(name(e))
    const version = cell?.version ?? 0
    if (e.ifVersion !== undefined && e.ifVersion !== version) {
      return { value: { isSet: false, version } }
    }
    cells.set(name(e), { value: e.value, version: version + 1 })
    return { value: { isSet: true, version: version + 1 } }
  })
  return {
    get: (key: string): unknown => cells.get(`ctxr-live/${key}`)?.value,
    set: (key: string, value: unknown) => {
      const version = (cells.get(`ctxr-live/${key}`)?.version ?? 0) + 1
      cells.set(`ctxr-live/${key}`, { value, version })
    },
  }
}

// the engine halves the mod calls on session.start: the cwd, the /ctx registration
export function sessionStart(on: On) {
  on('session.start', ($, e) => ({ cwd: e.cwd }))
  on('command.register', ($, e) => ({ value: { command: e.name } }))
}

// the engine's own drawing of a component the plugin yields (`next(e)`): an empty Box
export function engineDraws(on: On) {
  on('ui.render', ($, e) => {
    const { Box } = $.ui.resolve(e)
    return h(Box, {}) as RenderElement
  })
}
