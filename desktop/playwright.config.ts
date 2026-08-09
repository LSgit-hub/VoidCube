import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './tests/e2e',
  outputDir: './test-results',
  timeout: 120_000,
  expect: {
    timeout: 90_000
  },
  workers: 1,
  reporter: 'line'
})
