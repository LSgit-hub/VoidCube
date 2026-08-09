import { spawn } from 'node:child_process'
import type { RuntimePaths } from './runtime-locator'
import type {
  ServiceControlAction,
  ServiceControlResult,
  ExecutionContext,
  ServiceInfo,
  ServiceLifecycleAction
} from '../shared/contracts'

const CONTROL_TIMEOUT_MS = 120_000
const MAX_OUTPUT_LENGTH = 1_000_000

function isServiceInfo(value: unknown): value is ServiceInfo {
  if (!value || typeof value !== 'object') return false
  const service = value as Partial<ServiceInfo>
  return (
    typeof service.name === 'string' &&
    typeof service.port === 'number' &&
    (service.pid === undefined || service.pid === null || typeof service.pid === 'number') &&
    (service.state === 'healthy' || service.state === 'unhealthy' || service.state === 'stopped')
  )
}

function isExecutionContext(value: unknown): value is ExecutionContext {
  if (!value || typeof value !== 'object') return false
  const context = value as Partial<ExecutionContext>
  return (
    (context.mode === 'system' || context.mode === 'sandbox' || context.mode === 'remote') &&
    typeof context.backend === 'string' &&
    typeof context.hostPlatform === 'string' &&
    typeof context.hostWorkingDirectory === 'string' &&
    typeof context.backendWorkingDirectory === 'string' &&
    typeof context.workspaceName === 'string' &&
    typeof context.branch === 'string' &&
    typeof context.worktree === 'boolean' &&
    typeof context.workspaceMounted === 'boolean' &&
    typeof context.fallbackToLocal === 'boolean'
  )
}

export function parseServiceControlResult(output: string): ServiceControlResult {
  const value: unknown = JSON.parse(output)
  if (!value || typeof value !== 'object') throw new Error('Service control returned an invalid response')
  const result = value as Partial<ServiceControlResult>
  const validActions = new Set<ServiceControlAction>(['status', 'start', 'stop', 'restart'])
  if (
    result.schemaVersion !== 1 ||
    !result.action ||
    !validActions.has(result.action) ||
    typeof result.ok !== 'boolean' ||
    typeof result.generatedAt !== 'string' ||
    !Array.isArray(result.services) ||
    !result.services.every(isServiceInfo) ||
    (result.executionContext !== undefined && !isExecutionContext(result.executionContext)) ||
    (result.error !== undefined && typeof result.error !== 'string')
  ) {
    throw new Error('Service control returned an unsupported response')
  }
  return result as ServiceControlResult
}

function errorResult(action: ServiceControlAction, error: unknown): ServiceControlResult {
  return {
    schemaVersion: 1,
    action,
    ok: false,
    generatedAt: new Date().toISOString(),
    services: [],
    error: error instanceof Error ? error.message : String(error)
  }
}

export class ServiceController {
  private operation?: Promise<ServiceControlResult>
  private executionContext?: ExecutionContext

  constructor(private readonly runtime: RuntimePaths) {}

  status(): Promise<ServiceControlResult> {
    return this.invoke('status')
  }

  control(action: ServiceLifecycleAction): Promise<ServiceControlResult> {
    if (this.operation) return this.operation
    this.operation = this.invoke(action).finally(() => {
      this.operation = undefined
    })
    return this.operation
  }

  currentWorkspacePath(): string | undefined {
    return this.executionContext?.hostWorkingDirectory
  }

  private invoke(action: ServiceControlAction): Promise<ServiceControlResult> {
    return new Promise((resolve) => {
      const args = [
        ...this.runtime.pythonPrefixArgs,
        '-m',
        'VoidCube_cli.desktop_control',
        action
      ]
      const child = spawn(this.runtime.pythonCommand, args, {
        cwd: this.runtime.workingDirectory,
        env: {
          ...process.env,
          PYTHONUTF8: '1',
          VOIDCUBE_DESKTOP: '1'
        },
        windowsHide: true,
        stdio: ['ignore', 'pipe', 'pipe']
      })
      let stdout = ''
      let stderr = ''
      let settled = false

      const finish = (result: ServiceControlResult): void => {
        if (settled) return
        settled = true
        clearTimeout(timeout)
        resolve(result)
      }
      const timeout = setTimeout(() => {
        child.kill()
        finish(errorResult(action, `Service control timed out after ${CONTROL_TIMEOUT_MS / 1000}s`))
      }, CONTROL_TIMEOUT_MS)

      child.stdout.setEncoding('utf8')
      child.stderr.setEncoding('utf8')
      child.stdout.on('data', (data: string) => {
        if (stdout.length < MAX_OUTPUT_LENGTH) stdout += data
      })
      child.stderr.on('data', (data: string) => {
        if (stderr.length < MAX_OUTPUT_LENGTH) stderr += data
      })
      child.on('error', (error) => finish(errorResult(action, error)))
      child.on('close', () => {
        try {
          const result = parseServiceControlResult(stdout.trim())
          this.executionContext = result.executionContext
          if (!result.error && stderr.trim() && !result.ok) result.error = stderr.trim()
          finish(result)
        } catch (error) {
          const detail = stderr.trim()
          const message = detail || (error instanceof Error ? error.message : String(error))
          finish(errorResult(action, message))
        }
      })
    })
  }
}
