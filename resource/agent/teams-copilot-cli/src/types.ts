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

export interface AppConfig {
  browser: BrowserConfig;
  copilot: CopilotConfig;
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

export interface CommandResult {
  success: boolean;
  output: string;
  error?: string;
  exitCode: number;
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
