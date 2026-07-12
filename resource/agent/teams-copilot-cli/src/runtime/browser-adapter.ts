import type { BrowserConfig } from '../types.js';
import { spawn, type ChildProcess } from 'child_process';
import { chromium, type Browser } from 'playwright-core';
import http from 'http';
import { findChromiumBrowser, formatBrowserNotFoundError } from './browser-finder.js';

function hasCdpEndpoint(value: unknown): boolean {
  return (
    typeof value === 'object'
    && value !== null
    && 'webSocketDebuggerUrl' in value
    && typeof value.webSocketDebuggerUrl === 'string'
  );
}

function fetchJson(url: string): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const request = http.get(url, (res) => {
      let data = '';
      res.on('data', (chunk) => (data += chunk));
      res.on('end', () => {
        try { resolve(JSON.parse(data)); } catch (e) { reject(e); }
      });
    });
    request.setTimeout(2000, () => request.destroy(new Error('CDP request timed out')));
    request.on('error', reject);
  });
}

async function waitForCdp(port: number, timeoutMs = 30000): Promise<void> {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const version = await fetchJson(`http://localhost:${port}/json/version`);
      if (hasCdpEndpoint(version)) return;
    } catch {}
    await new Promise((r) => setTimeout(r, 500));
  }
  throw Object.assign(new Error(`CDP not ready on port ${port} after ${timeoutMs}ms`), { code: 'BROWSER_LAUNCH_FAILED' });
}

async function isCdpReady(port: number): Promise<boolean> {
  try {
    const version = await fetchJson(`http://localhost:${port}/json/version`);
    return hasCdpEndpoint(version);
  } catch {
    return false;
  }
}

export async function launchBrowser(config: BrowserConfig): Promise<{ pid: number; port: number }> {
  if (await isCdpReady(config.port)) {
    return { pid: 0, port: config.port };
  }

  const browserPath = findChromiumBrowser(config.path);
  if (!browserPath) {
    throw Object.assign(new Error(formatBrowserNotFoundError()), { code: 'BROWSER_LAUNCH_FAILED' });
  }

  const proc: ChildProcess = spawn(browserPath, [
    `--remote-debugging-port=${config.port}`,
    `--user-data-dir=${config.userDataDir}`,
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-extensions',
  ], {
    detached: true,
    stdio: 'ignore',
  });
  proc.unref();

  const spawnError = new Promise<never>((_, reject) => {
    proc.once('error', (error) => {
      reject(Object.assign(error, { code: 'BROWSER_LAUNCH_FAILED' }));
    });
  });
  try {
    await Promise.race([waitForCdp(config.port), spawnError]);
  } catch (error) {
    terminateBrowserProcess(proc.pid || 0);
    throw error;
  }
  return { pid: proc.pid || 0, port: config.port };
}

export async function connectToBrowser(port: number): Promise<Browser> {
  try {
    return await chromium.connectOverCDP(`http://localhost:${port}`);
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : String(error);
    throw Object.assign(new Error(`Failed to connect to browser on port ${port}: ${message}`), { code: 'BROWSER_CONNECT_FAILED' });
  }
}

export async function closeBrowser(browser: Browser): Promise<void> {
  await browser.close();
}

export function terminateBrowserProcess(pid: number): void {
  if (pid <= 0) return;
  try {
    process.kill(process.platform === 'win32' ? pid : -pid, 'SIGTERM');
  } catch {
    try {
      process.kill(pid, 'SIGTERM');
    } catch {}
  }
}
