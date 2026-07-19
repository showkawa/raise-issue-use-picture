import type { AppConfig } from '../types.js';
import type { Provider } from './types.js';
import { ProxyProvider } from './proxy.js';
import { MockProvider } from './mock.js';

export function createProvider(config: AppConfig): Provider {
  switch (config.provider) {
    case 'proxy':
      return new ProxyProvider(config.proxy);
    case 'mock':
      return new MockProvider();
    default:
      throw new Error(`Unknown provider: ${String(config.provider)}`);
  }
}
