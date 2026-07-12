import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: 'test/e2e',
  timeout: 60000,
  expect: { timeout: 10000 },
  retries: 0,
  use: {
    headless: false,
    viewport: { width: 1280, height: 720 },
    actionTimeout: 15000,
  },
});
