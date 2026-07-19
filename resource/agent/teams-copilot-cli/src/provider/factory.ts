import type { AppConfig, RuntimeStatusHandler } from '../types.js';
import type { Provider } from './types.js';
import { CopilotWebProvider } from './copilot-web/index.js';
import { ProxyProvider } from './proxy.js';
import { MockProvider } from './mock.js';

export function createProvider(
  config: AppConfig,
  onStatus?: RuntimeStatusHandler,
): Provider {
  switch (config.provider) {
    case 'proxy':
      return new ProxyProvider(config.proxy);
    case 'copilot-web':
      return new CopilotWebProvider(config, onStatus);
    case 'mock':
      return new MockProvider();
    default:
      throw new Error(`Unknown provider: ${String(config.provider)}`);
  }
}
