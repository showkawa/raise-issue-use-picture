import type { AppConfig, BrowserConfig } from '../types.js';
import { readFileSync, existsSync } from 'fs';
import { load as yamlLoad } from 'js-yaml';
import { join } from 'path';
import { homedir } from 'os';

const DEFAULTS: AppConfig = {
  browser: {
    port: 9222,
    userDataDir: join(homedir(), '.teams-copilot', 'profile'),
  },
  copilot: {
    teamsUrl: 'https://teams.microsoft.com/',
    copilotUrl: 'https://teams.microsoft.com/v2/',
    selectors: {
      inputArea: '[contenteditable="true"], [role="textbox"], textarea[aria-label*="message"], textarea[aria-label*="消息"]',
      sendButton: 'button[aria-label*="Send"], button[aria-label*="发送"], button[type="submit"]',
      responseContainer: '[data-content="ai-message"], [data-tid="chat-pane-message"], [data-tid="chat-window"], [role="log"], [aria-live="polite"]',
      copilotEntry: 'button[aria-label*="Copilot"], a[aria-label*="Copilot"], [data-tid*="copilot"], [aria-label*="Microsoft 365 Copilot"]',
      loginIndicator: '[data-tid="login"], input[type="email"], input[name="loginfmt"]',
    },
    timeouts: {
      pageLoad: 30000,
      copilotLoad: 15000,
      streaming: 120000,
      pollingInterval: 500,
    },
  },
};

interface LegacyConfig {
  edge?: {
    executablePath?: string;
    userDataDir?: string;
    debuggingPort?: number;
  };
  copilot?: {
    url?: string;
    inputSelector?: string;
    sendButtonSelector?: string;
    messageSelector?: string;
    timeout?: number;
  };
}

function definedEntries<T extends Record<string, unknown>>(value: T): Partial<T> {
  return Object.fromEntries(
    Object.entries(value).filter(([, entryValue]) => entryValue !== undefined),
  ) as Partial<T>;
}

function validate(config: AppConfig): void {
  if (!config.copilot.teamsUrl) throw new Error('Missing required field: copilot.teamsUrl');
  if (!config.copilot.copilotUrl) throw new Error('Missing required field: copilot.copilotUrl');
  if (
    !Number.isInteger(config.browser.port)
    || config.browser.port < 1
    || config.browser.port > 65535
  ) {
    throw new Error('Invalid field: browser.port');
  }
  if (!config.browser.userDataDir) {
    throw new Error('Missing required field: browser.userDataDir');
  }
  for (const [name, selector] of Object.entries(config.copilot.selectors)) {
    if (!selector) throw new Error(`Missing required field: copilot.selectors.${name}`);
  }
  for (const [name, timeout] of Object.entries(config.copilot.timeouts)) {
    if (!Number.isFinite(timeout) || timeout <= 0) {
      throw new Error(`Invalid field: copilot.timeouts.${name}`);
    }
  }
}

export function loadConfig(configPath?: string): AppConfig {
  const path = configPath ?? join(process.cwd(), 'config.yaml');
  if (!existsSync(path)) {
    return {
      ...DEFAULTS,
      browser: { ...DEFAULTS.browser },
      copilot: {
        ...DEFAULTS.copilot,
        selectors: { ...DEFAULTS.copilot.selectors },
        timeouts: { ...DEFAULTS.copilot.timeouts },
      },
    };
  }
  try {
    const parsed = yamlLoad(readFileSync(path, 'utf8'));
    const raw = (
      typeof parsed === 'object' && parsed !== null ? parsed : {}
    ) as Partial<AppConfig> & LegacyConfig;
    const legacyBrowser: Partial<BrowserConfig> = raw.edge
      ? definedEntries({
          path: raw.edge.executablePath,
          port: raw.edge.debuggingPort,
          userDataDir: raw.edge.userDataDir,
        })
      : {};
    const rawCopilot = raw.copilot;
    const hasLegacyCopilot = Boolean(
      rawCopilot?.url
      || rawCopilot?.inputSelector
      || rawCopilot?.sendButtonSelector
      || rawCopilot?.messageSelector
      || rawCopilot?.timeout,
    );
    const legacyCopilot = hasLegacyCopilot && rawCopilot
      ? {
          ...definedEntries({
            teamsUrl: rawCopilot.url,
            copilotUrl: rawCopilot.url,
          }),
          selectors: definedEntries({
            inputArea: rawCopilot.inputSelector,
            sendButton: rawCopilot.sendButtonSelector,
            responseContainer: rawCopilot.messageSelector,
          }),
          timeouts: definedEntries({
            streaming: rawCopilot.timeout,
          }),
        }
      : {};
    const config: AppConfig = {
      browser: { ...DEFAULTS.browser, ...legacyBrowser, ...raw?.browser },
      copilot: {
        ...DEFAULTS.copilot,
        ...legacyCopilot,
        ...raw?.copilot,
        selectors: {
          ...DEFAULTS.copilot.selectors,
          ...(legacyCopilot as Partial<AppConfig['copilot']>).selectors,
          ...raw?.copilot?.selectors,
        },
        timeouts: {
          ...DEFAULTS.copilot.timeouts,
          ...(legacyCopilot as Partial<AppConfig['copilot']>).timeouts,
          ...raw?.copilot?.timeouts,
        },
      },
    };
    validate(config);
    return config;
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : String(error);
    if (message.startsWith('Missing required field') || message.startsWith('Invalid field')) throw error;
    throw new Error(`Failed to parse config.yaml: ${message}`);
  }
}

export function mergeCliFlags(config: AppConfig, flags: Partial<BrowserConfig>): AppConfig {
  const merged = {
    ...config,
    browser: { ...config.browser, ...flags },
  };
  validate(merged);
  return merged;
}
