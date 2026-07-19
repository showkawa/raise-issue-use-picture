export interface ChatTurnOptions {
  onUpdate?: (chunk: string) => void;
  timeoutMs?: number;
}

export interface ChatTurnResult {
  text: string;
}

export interface CreateSessionOptions {
  /** Optional system persona seeded at the start of the conversation. */
  systemPrompt?: string;
}

export interface ChatSession {
  /** Send one message in the same conversation context. */
  send(message: string, options?: ChatTurnOptions): Promise<ChatTurnResult>;
  close(): Promise<void>;
}

export interface Provider {
  readonly id: string;
  init(): Promise<void>;
  createSession(options?: CreateSessionOptions): Promise<ChatSession>;
  close(): Promise<void>;
}
