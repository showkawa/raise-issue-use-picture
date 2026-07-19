export type ProviderId = 'proxy' | 'mock';

export interface ProxyConfig {
  /** OpenAI-compatible base URL of teams-copilot-proxy, including /v1. */
  baseUrl: string;
  /** Model name forwarded to the proxy. */
  model: string;
  /** API key placeholder; the proxy ignores it but the OpenAI shape needs one. */
  apiKey: string;
  /** Per-request timeout in milliseconds. */
  timeoutMs: number;
}

export interface AppConfig {
  provider: ProviderId;
  proxy: ProxyConfig;
}
