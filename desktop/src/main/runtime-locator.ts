import { existsSync } from 'node:fs'
import { homedir } from 'node:os'
import { delimiter, join, resolve } from 'node:path'

export interface RuntimePaths {
  projectRoot?: string
  pythonCommand: string
  pythonPrefixArgs: string[]
  cliArgs: string[]
  workingDirectory: string
}

type Platform = NodeJS.Platform

function isProjectRoot(path: string): boolean {
  return existsSync(join(path, 'src', 'voidcube')) && existsSync(join(path, 'pyproject.toml'))
}

export function findProjectRoot(candidates: Array<string | undefined>): string | undefined {
  for (const candidate of candidates) {
    if (!candidate) continue
    const resolved = resolve(candidate)
    if (isProjectRoot(resolved)) return resolved
  }
  return undefined
}

function projectPython(projectRoot: string, platform: Platform): string | undefined {
  const candidates = platform === 'win32'
    ? [join(projectRoot, '.venv', 'Scripts', 'python.exe')]
    : [join(projectRoot, '.venv', 'bin', 'python3'), join(projectRoot, '.venv', 'bin', 'python')]
  return candidates.find((candidate) => existsSync(candidate))
}

function executableFromPath(name: string, envPath: string | undefined, platform: Platform): string | undefined {
  const extensions = platform === 'win32' ? ['', '.exe'] : ['']
  for (const directory of (envPath ?? '').split(delimiter)) {
    if (!directory) continue
    for (const extension of extensions) {
      const candidate = join(directory, `${name}${extension}`)
      if (existsSync(candidate)) return candidate
    }
  }
  return undefined
}

export function resolveRuntimePaths(options: {
  projectRoot?: string
  resourcesPath: string
  env?: NodeJS.ProcessEnv
  platform?: Platform
}): RuntimePaths {
  const env = options.env ?? process.env
  const platform = options.platform ?? process.platform
  const bundledRoot = join(options.resourcesPath, 'voidcube')
  const projectRoot = options.projectRoot ?? (isProjectRoot(bundledRoot) ? bundledRoot : undefined)
  const configuredPython = env.VOIDCUBE_DESKTOP_PYTHON?.trim()
  const bundledPython = platform === 'win32'
    ? join(options.resourcesPath, 'python', 'python.exe')
    : join(options.resourcesPath, 'python', 'bin', 'python3')
  const localPython = projectRoot ? projectPython(projectRoot, platform) : undefined
  const pathPython = executableFromPath(platform === 'win32' ? 'python' : 'python3', env.PATH, platform)
  const pythonCommand = configuredPython || (existsSync(bundledPython) ? bundledPython : undefined) || localPython || pathPython || (platform === 'win32' ? 'python.exe' : 'python3')
  const workingDirectory = resolve(env.VOIDCUBE_DESKTOP_WORKSPACE?.trim() || projectRoot || homedir())
  const cliArgs = ['-m', 'voidcube.interfaces.cli.main']

  return {
    projectRoot,
    pythonCommand,
    pythonPrefixArgs: [],
    cliArgs,
    workingDirectory
  }
}

export function normalizeMonitorUrl(value: string | undefined): string {
  const fallback = 'http://127.0.0.1:6002/ui'
  const parsed = new URL(value?.trim() || fallback)
  const allowedHosts = new Set(['127.0.0.1', 'localhost', '[::1]'])
  if (parsed.protocol !== 'http:' || !allowedHosts.has(parsed.hostname)) {
    throw new Error('Supervisor URL must use HTTP on the local loopback interface')
  }
  parsed.hash = ''
  return parsed.toString()
}
