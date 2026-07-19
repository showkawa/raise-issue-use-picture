export interface BrowserConfig {
  path?: string;
  port: number;
  userDataDir: string;
}

export interface CopilotSelectors {
  inputArea: string;
  sendButton: string;
  responseContainer: string;
  loginIndicator: string;
  fileInput: string;
}

export interface CopilotTimeouts {
  pageLoad: number;
  copilotLoad: number;
  streaming: number;
  pollingInterval: number;
}

export type CopilotResponseMode = 'auto' | 'signalr' | 'dom';
export type CopilotRequestMode = 'auto' | 'browser-api' | 'dom';

export interface CopilotConfig {
  copilotUrl: string;
  requestMode: CopilotRequestMode;
  responseMode: CopilotResponseMode;
  selectors: CopilotSelectors;
  timeouts: CopilotTimeouts;
}

export type PermissionMode = 'yolo' | 'allowlist' | 'ask';

export interface AgentConfig {
  permissionMode: PermissionMode;
  maxIterations: number;
  maxContinuations: number;
  maxTurnsPerConversation: number;
  minSendIntervalMs: number;
  maxMessageChars: number;
  denyCommands: string[];
  allowCommands: string[];
  /** Per-session bidirectional character budget before proactive rotation. */
  sessionCharBudget?: number;
}

export type ProviderId = 'proxy' | 'copilot-web' | 'mock';

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
  browser: BrowserConfig;
  copilot: CopilotConfig;
  proxy: ProxyConfig;
  provider: ProviderId;
  agent: AgentConfig;
}

export interface InjectResult {
  success: boolean;
  method: 'clipboard' | 'insertText';
  error?: string;
}

export interface StreamResult {
  text: string;
  truncated: boolean;
  duration: number;
}

export interface AskOptions {
  onUpdate?: (chunk: string) => void;
  autoContinue?: boolean;
  maxContinuations?: number;
}

export type RuntimeStatusHandler = (message: string) => void;

export interface CopilotSession {
  ask(prompt: string, options?: AskOptions): Promise<StreamResult>;
  askWithFile(filePath: string, prompt: string, options?: AskOptions): Promise<StreamResult>;
  close(): Promise<void>;
}
