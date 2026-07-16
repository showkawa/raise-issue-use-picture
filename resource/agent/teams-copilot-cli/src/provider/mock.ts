import type {
  ChatSession,
  ChatTurnOptions,
  ChatTurnResult,
  Provider,
  ProviderCapabilities,
} from './types.js';

export interface MockTurn {
  response: string;
  truncated?: boolean;
}

export interface MockProviderOptions {
  capabilities?: Partial<ProviderCapabilities>;
  /** Optional dynamic responder; overrides the scripted turns when provided. */
  respond?: (message: string, turnIndex: number) => string | MockTurn;
}

const DEFAULT_CAPABILITIES: ProviderCapabilities = {
  maxMessageChars: 8000,
  supportsStreaming: true,
  supportsSystemPrompt: false,
};

/** Replays scripted responses in order. Records every message it receives. */
export class MockProvider implements Provider {
  readonly id = 'mock';
  readonly sent: string[] = [];
  private script: MockTurn[];
  private cursor = 0;
  private options: MockProviderOptions;
  private alive = true;

  constructor(script: Array<string | MockTurn> = [], options: MockProviderOptions = {}) {
    this.script = script.map((turn) => (typeof turn === 'string' ? { response: turn } : turn));
    this.options = options;
  }

  async init(): Promise<void> {}

  async createSession(): Promise<ChatSession> {
    const provider = this;
    return {
      async send(message: string, options: ChatTurnOptions = {}): Promise<ChatTurnResult> {
        provider.sent.push(message);
        const index = provider.cursor++;
        let turn: MockTurn;
        if (provider.options.respond) {
          const replied = provider.options.respond(message, index);
          turn = typeof replied === 'string' ? { response: replied } : replied;
        } else {
          const scripted = provider.script[index];
          if (!scripted) {
            throw new Error(`MockProvider script exhausted at turn ${index}`);
          }
          turn = scripted;
        }
        options.onUpdate?.(turn.response);
        return { text: turn.response, truncated: turn.truncated ?? false, duration: 0 };
      },
      async healthy(): Promise<boolean> {
        return provider.alive;
      },
      async close(): Promise<void> {},
    };
  }

  markUnhealthy(): void {
    this.alive = false;
  }

  markHealthy(): void {
    this.alive = true;
  }

  async close(): Promise<void> {}

  capabilities(): ProviderCapabilities {
    return { ...DEFAULT_CAPABILITIES, ...this.options.capabilities };
  }
}
