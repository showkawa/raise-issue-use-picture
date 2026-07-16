import { test, expect } from '@playwright/test';
import {
  launchBrowser,
  connectToBrowser,
  closeBrowser,
  terminateBrowserProcess,
} from '../../dist/provider/copilot-web/browser-adapter.js';
import { findChromiumBrowser } from '../../dist/provider/copilot-web/browser-finder.js';
import { loadConfig } from '../../dist/provider/copilot-web/config.js';
import { accessSync, constants } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';
import { randomBytes } from 'crypto';
import { createServer } from 'net';

function uniqueUserDataDir(): string {
  const suffix = randomBytes(4).toString('hex');
  return join(tmpdir(), `teams-copilot-e2e-${suffix}`);
}

async function getFreePort(): Promise<number> {
  const server = createServer();
  await new Promise<void>((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => resolve());
  });
  const address = server.address();
  const port = typeof address === 'object' && address ? address.port : 0;
  await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
  return port;
}

test.describe('E2E Smoke', () => {
  test('browser launch and connect', async () => {
    const browserPath = findChromiumBrowser();
    test.skip(!browserPath, 'No Chromium browser found');

    const port = await getFreePort();
    const config = {
      path: browserPath,
      port,
      userDataDir: uniqueUserDataDir(),
    };

    const { pid, port: actualPort } = await launchBrowser(config);
    const browser = await connectToBrowser(actualPort);
    try {
      expect(actualPort).toBe(port);
      expect(browser.isConnected()).toBe(true);
    } finally {
      await closeBrowser(browser);
      terminateBrowserProcess(pid);
    }
    expect(browser.isConnected()).toBe(false);
  });

  test('navigate to a test page', async () => {
    const browserPath = findChromiumBrowser();
    test.skip(!browserPath, 'No Chromium browser found');

    const port = await getFreePort();
    const config = {
      path: browserPath,
      port,
      userDataDir: uniqueUserDataDir(),
    };

    const { pid, port: actualPort } = await launchBrowser(config);
    const browser = await connectToBrowser(actualPort);
    try {
      const page = await browser.newPage();
      await page.goto('data:text/html,<title>M365 Copilot E2E</title>');
      await expect(page).toHaveTitle('M365 Copilot E2E');
      await page.close();
    } finally {
      await closeBrowser(browser);
      terminateBrowserProcess(pid);
    }
  });

  test('config loading returns valid config with required fields', () => {
    const config = loadConfig();

    expect(config.browser).toBeDefined();
    expect(typeof config.browser.port).toBe('number');
    expect(config.browser.port).toBeGreaterThan(0);
    expect(typeof config.browser.userDataDir).toBe('string');

    expect(config.copilot).toBeDefined();
    expect(typeof config.copilot.copilotUrl).toBe('string');
    expect(config.copilot.copilotUrl.length).toBeGreaterThan(0);

    expect(config.copilot.selectors).toBeDefined();
    expect(typeof config.copilot.selectors.inputArea).toBe('string');
    expect(typeof config.copilot.selectors.sendButton).toBe('string');

    expect(config.copilot.timeouts).toBeDefined();
    expect(typeof config.copilot.timeouts.pageLoad).toBe('number');
    expect(typeof config.copilot.timeouts.streaming).toBe('number');
  });

  test('browser finder returns a valid browser path', () => {
    const path = findChromiumBrowser();
    test.skip(!path, 'No Chromium browser found');

    expect(() => accessSync(path, constants.X_OK)).not.toThrow();
  });
});
