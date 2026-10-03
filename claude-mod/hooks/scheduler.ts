export type SchedulerDeps = {
  after: (ms: number, fn: () => void) => { cancel: () => void }
  run: () => Promise<void> // one refresh; must not reject
}

// debounce + single-flight: at most one ctxr process at a time; pokes during a run
// coalesce into one follow-up run, so the map always converges on the latest state
export class Scheduler {
  private timer: { cancel: () => void } | null = null
  private isRunning = false
  private isDirty = false

  constructor(private readonly deps: SchedulerDeps, private readonly debounceMs: number) {}

  poke(): void {
    if (this.isRunning) {
      this.isDirty = true
      return
    }
    this.timer?.cancel()
    this.timer = this.deps.after(this.debounceMs, () => {
      this.timer = null
      void this.fire()
    })
  }

  private async fire(): Promise<void> {
    this.isRunning = true
    try {
      await this.deps.run()
    } finally {
      this.isRunning = false
      if (this.isDirty) {
        this.isDirty = false
        this.poke()
      }
    }
  }
}
