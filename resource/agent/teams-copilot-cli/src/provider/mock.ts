import type {
  ChatSession,
  ChatTurnOptions,
  ChatTurnResult,
  CreateSessionOptions,
  Provider,
} from './types.js';

export interface MockProviderOptions {
  /** Optional dynamic responder; overrides the scripted turns when provided. */
  respond?: (message: string, turnIndex: number) => string;
}

/** Replays scripted responses in order. Records every message it receives. */
export class MockProvider implements Provider {
  readonly id = 'mock';
  readonly sent: string[] = [];
  readonly systemPrompts: Array<string | undefined> = [];
  private script: string[];
  private cursor = 0;
  private options: MockProviderOptions;

  constructor(script: string[] = [], options: MockProviderOptions = {}) {
    this.script = script;
    this.options = options;
  }

  async init(): Promise<void> {}

  async createSession(options: CreateSessionOptions = {}): Promise<ChatSession> {
    const provider = this;
    provider.systemPrompts.push(options.systemPrompt);
    return {
      async send(message: string, options: ChatTurnOptions = {}): Promise<ChatTurnResult> {
        provider.sent.push(message);
        const index = provider.cursor++;
        let text: string;
        if (provider.options.respond) {
          text = provider.options.respond(message, index);
        } else {
          const scripted = provider.script[index];
          if (scripted === undefined) {
            throw new Error(`MockProvider script exhausted at turn ${index}`);
          }
          text = scripted;
        }
        options.onUpdate?.(text);
        return { text };
      },
      async close(): Promise<void> {},
    };
  }

  async close(): Promise<void> {}
}
