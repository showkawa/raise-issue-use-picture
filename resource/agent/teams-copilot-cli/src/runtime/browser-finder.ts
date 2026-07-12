import { accessSync, constants } from 'fs';

const WIN_PATHS = [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
];

const MAC_PATHS = [
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
];

const LINUX_PATHS = [
  '/usr/bin/google-chrome',
  '/usr/bin/google-chrome-stable',
  '/usr/bin/microsoft-edge',
  '/usr/bin/microsoft-edge-stable',
  '/usr/bin/chromium-browser',
  '/usr/bin/chromium',
];

function getPlatformPaths(): string[] {
  if (process.platform === 'win32') return WIN_PATHS;
  if (process.platform === 'darwin') return MAC_PATHS;
  return LINUX_PATHS;
}

function pathExists(filePath: string): boolean {
  try {
    accessSync(filePath, constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

export function findChromiumBrowser(preferred?: string): string | null {
  const envPath = process.env.TEAMS_COPILOT_BROWSER;
  if (envPath && pathExists(envPath)) return envPath;
  if (preferred && pathExists(preferred)) return preferred;
  for (const p of getPlatformPaths()) {
    if (pathExists(p)) return p;
  }
  return null;
}

export function formatBrowserNotFoundError(): string {
  const paths = getPlatformPaths();
  return `No Chromium browser found. Searched:\n${paths.map(p => `  - ${p}`).join('\n')}\n\nSet TEAMS_COPILOT_BROWSER env var or configure browser.path in config.yaml`;
}
