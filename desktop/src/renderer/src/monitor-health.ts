export type MonitorHealthDecision = 'keep' | 'stale'

export class MonitorHealthGate {
  private consecutiveFailures = 0

  constructor(private readonly failureLimit = 3) {
    if (!Number.isInteger(failureLimit) || failureLimit < 1) {
      throw new Error('Monitor failure limit must be a positive integer')
    }
  }

  observe(ready: boolean): MonitorHealthDecision {
    if (ready) {
      this.reset()
      return 'keep'
    }

    this.consecutiveFailures += 1
    return this.consecutiveFailures >= this.failureLimit ? 'stale' : 'keep'
  }

  reset(): void {
    this.consecutiveFailures = 0
  }
}
