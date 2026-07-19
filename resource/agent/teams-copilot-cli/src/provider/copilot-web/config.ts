import type { AgentConfig, AppConfig, BrowserConfig, ProxyConfig } from '../../types.js';
import { readFileSync, existsSync } from 'fs';
import { load as yamlLoad } from 'js-yaml';
import { join } from 'path';
import { homedir } from 'os';

const PROXY_DEFAULTS: ProxyConfig = {
  baseUrl: 'http://127.0.0.1:8000/v1',
  model: 'm365-copilot',
  apiKey: 'unused',
  timeoutMs: 120000,
};

const AGENT_DEFAULTS: AgentConfig = {
  permissionMode: 'allowlist',
  maxIterations: 25,
  maxContinuations: 4,
  maxTurnsPerConversation: 30,
  minSendIntervalMs: 3000,
  maxMessageChars: 8000,
  denyCommands: [
    'rm -rf',
    'Remove-Item -Recurse',
    'git push',
    'git reset --hard',
    'npm publish',
  ],
  allowCommands: [],
  sessionCharBudget: 40000,
};

const DEFAULTS: AppConfig = {
  provider: 'proxy',
  proxy: PROXY_DEFAULTS,
  agent: AGENT_DEFAULTS,
  browser: {
    port: 9222,
    userDataDir: join(homedir(), '.teams-copilot', 'profile'),
  },
  copilot: {
    copilotUrl: 'https://m365.cloud.microsoft/chat',
    requestMode: 'auto',
    responseMode: 'auto',
    selectors: {
      inputArea: '#m365-chat-editor-target-element, [role="textbox"][contenteditable="true"][aria-label*="Copilot"], [role="textbox"][contenteditable="true"][aria-label*="消息"]',
      sendButton: 'button[type="submit"][aria-label*="Send"], button[type="submit"][aria-label*="发送"], button[aria-label*="Send message"], button[aria-label*="发送消息"]',
      responseContainer: '[data-testid="lastChatMessage"] [data-testid="markdown-reply"], [data-testid="lastChatMessage"], [data-testid="markdown-reply"], [data-content="ai-message"]',
      loginIndicator: 'input[type="email"], input[name="loginfmt"], input[type="password"], input[name="passwd"], [data-tid="sign-in-button"]',
      fileInput: '#upload-file-button, input[type="file"]',
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
  if (
    config.provider !== 'proxy'
    && config.provider !== 'copilot-web'
    && config.provider !== 'mock'
  ) {
    throw new Error('Invalid field: provider');
  }
  if (!config.proxy.baseUrl) throw new Error('Missing required field: proxy.baseUrl');
  if (!config.proxy.model) throw new Error('Missing required field: proxy.model');
  if (!Number.isFinite(config.proxy.timeoutMs) || config.proxy.timeoutMs <= 0) {
    throw new Error('Invalid field: proxy.timeoutMs');
  }
  const { agent } = config;
  if (
    agent.permissionMode !== 'yolo'
    && agent.permissionMode !== 'allowlist'
    && agent.permissionMode !== 'ask'
  ) {
    throw new Error('Invalid field: agent.permissionMode');
  }
  for (const name of [
    'maxIterations',
    'maxContinuations',
    'maxTurnsPerConversation',
    'minSendIntervalMs',
    'maxMessageChars',
  ] as const) {
    if (!Number.isFinite(agent[name]) || agent[name] < 0) {
      throw new Error(`Invalid field: agent.${name}`);
    }
  }
  if (!Array.isArray(agent.denyCommands) || !Array.isArray(agent.allowCommands)) {
    throw new Error('Invalid field: agent.denyCommands');
  }
}

export function loadConfig(configPath?: string): AppConfig {
  const path = configPath ?? join(process.cwd(), 'config.yaml');
  if (!existsSync(path)) {
    return {
      ...DEFAULTS,
      browser: { ...DEFAULTS.browser },
      proxy: { ...DEFAULTS.proxy },
      copilot: {
        ...DEFAULTS.copilot,
        selectors: { ...DEFAULTS.copilot.selectors },
        timeouts: { ...DEFAULTS.copilot.timeouts },
      },
      agent: { ...AGENT_DEFAULTS, denyCommands: [...AGENT_DEFAULTS.denyCommands], allowCommands: [...AGENT_DEFAULTS.allowCommands] },
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
      provider: raw?.provider ?? DEFAULTS.provider,
      agent: {
        ...AGENT_DEFAULTS,
        ...(raw as Partial<AppConfig>)?.agent,
        denyCommands: (raw as Partial<AppConfig>)?.agent?.denyCommands ?? [...AGENT_DEFAULTS.denyCommands],
        allowCommands: (raw as Partial<AppConfig>)?.agent?.allowCommands ?? [...AGENT_DEFAULTS.allowCommands],
      },
      proxy: { ...PROXY_DEFAULTS, ...raw?.proxy },
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
    if (
      config.copilot.requestMode !== 'auto'
      && config.copilot.requestMode !== 'browser-api'
      && config.copilot.requestMode !== 'dom'
    ) {
      throw new Error('Invalid field: copilot.requestMode');
    }
    if (
      config.copilot.responseMode !== 'auto'
      && config.copilot.responseMode !== 'signalr'
      && config.copilot.responseMode !== 'dom'
    ) {
      throw new Error('Invalid field: copilot.responseMode');
    }
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
