import { defineConfig } from '@playwright/test'
import { existsSync } from 'node:fs'
import { resolve } from 'node:path'

const projectRoot = resolve(__dirname, '..')
const venvPython = resolve(
  projectRoot,
  '.venv',
  process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python'
)
const pythonCommand = process.env.VOIDCUBE_TEST_PYTHON || (existsSync(venvPython) ? venvPython : 'python')
const supervisorPort = Number(process.env.VOIDCUBE_PLAYWRIGHT_SUPERVISOR_PORT || 6002)
const supervisorUrl = `http://127.0.0.1:${supervisorPort}`
const supervisorTestHome = resolve(projectRoot, '.local_state', 'playwright-supervisor')

export default defineConfig({
  testDir: './tests/e2e',
  outputDir: './test-results',
  timeout: 120_000,
  expect: {
    timeout: 90_000
  },
  workers: 1,
  reporter: 'line',
  webServer: {
    command: `"${pythonCommand}" desktop/tests/e2e/support/supervisor_server.py --host 127.0.0.1 --port ${supervisorPort}`,
    cwd: projectRoot,
    url: `${supervisorUrl}/ui`,
    timeout: 120_000,
    reuseExistingServer: true,
    env: {
      ...process.env,
      PYTEST_CURRENT_TEST: 'playwright-supervisor',
      VOIDCUBE_HOME: supervisorTestHome
    }
  }
})
