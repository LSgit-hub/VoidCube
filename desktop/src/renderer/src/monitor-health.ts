export type MonitorHealthDecision = 'keep' | 'replace'

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
    return this.consecutiveFailures >= this.failureLimit ? 'replace' : 'keep'
  }

  reset(): void {
    this.consecutiveFailures = 0
  }
}
