import { readFileSync, existsSync } from 'fs';
import { join } from 'path';
import { load as yamlLoad } from 'js-yaml';
import type { AppConfig, ProviderId, ProxyConfig } from './types.js';

const PROXY_DEFAULTS: ProxyConfig = {
  baseUrl: 'http://127.0.0.1:8000/v1',
  model: 'm365-copilot',
  apiKey: 'unused',
  timeoutMs: 120000,
};

const DEFAULTS: AppConfig = {
  provider: 'proxy',
  proxy: PROXY_DEFAULTS,
};

function isProviderId(value: unknown): value is ProviderId {
  return value === 'proxy' || value === 'mock';
}

function validate(config: AppConfig): void {
  if (!isProviderId(config.provider)) throw new Error('Invalid field: provider');
  if (!config.proxy.baseUrl) throw new Error('Missing required field: proxy.baseUrl');
  if (!config.proxy.model) throw new Error('Missing required field: proxy.model');
  if (!config.proxy.apiKey) throw new Error('Missing required field: proxy.apiKey');
  if (!Number.isFinite(config.proxy.timeoutMs) || config.proxy.timeoutMs <= 0) {
    throw new Error('Invalid field: proxy.timeoutMs');
  }
}

export function loadConfig(configPath?: string): AppConfig {
  const path = configPath ?? join(process.cwd(), 'config.yaml');
  if (!existsSync(path)) {
    return { provider: DEFAULTS.provider, proxy: { ...PROXY_DEFAULTS } };
  }
  try {
    const parsed = yamlLoad(readFileSync(path, 'utf8'));
    const raw = (typeof parsed === 'object' && parsed !== null ? parsed : {}) as Partial<AppConfig>;
    const config: AppConfig = {
      provider: raw.provider ?? DEFAULTS.provider,
      proxy: { ...PROXY_DEFAULTS, ...raw.proxy },
    };
    validate(config);
    return config;
  } catch (error: unknown) {
    const message = error instanceof Error ? error.message : String(error);
    if (message.startsWith('Missing required field') || message.startsWith('Invalid field')) {
      throw error;
    }
    throw new Error(`Failed to parse config.yaml: ${message}`);
  }
}
