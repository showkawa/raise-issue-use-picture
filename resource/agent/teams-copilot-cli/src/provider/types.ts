export interface ChatTurnOptions {
  onUpdate?: (chunk: string) => void;
  timeoutMs?: number;
}

export interface ChatTurnResult {
  text: string;
  truncated: boolean;
  duration: number;
}

export interface ChatSession {
  /** Send one message in the same conversation context (with auto-continuation). */
  send(message: string, options?: ChatTurnOptions): Promise<ChatTurnResult>;
  /** Whether the session is still healthy (login state, page/connection alive). */
  healthy(): Promise<boolean>;
  close(): Promise<void>;
}

export interface ProviderCapabilities {
  maxMessageChars: number;
  supportsStreaming: boolean;
  supportsSystemPrompt: boolean;
}

export interface Provider {
  readonly id: string;
  init(): Promise<void>;
  createSession(): Promise<ChatSession>;
  close(): Promise<void>;
  capabilities(): ProviderCapabilities;
}
