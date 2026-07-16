import type { AppConfig, RuntimeStatusHandler } from '../../types.js';
import type {
  ChatSession,
  ChatTurnOptions,
  ChatTurnResult,
  Provider,
  ProviderCapabilities,
} from '../types.js';
import { SessionManager } from './session-manager.js';
import { loadConfig } from './config.js';

export class CopilotWebProvider implements Provider {
  readonly id = 'copilot-web';
  private config: AppConfig;
  private manager: SessionManager;

  constructor(config?: AppConfig, onStatus?: RuntimeStatusHandler) {
    this.config = config ?? loadConfig();
    this.manager = new SessionManager(this.config, onStatus);
  }

  async init(): Promise<void> {
    await this.manager.init();
  }

  async createSession(): Promise<ChatSession> {
    const session = await this.manager.createSession();
    const manager = this.manager;
    const { maxContinuations } = this.config.agent;
    return {
      async send(message: string, options: ChatTurnOptions = {}): Promise<ChatTurnResult> {
        const result = await session.ask(message, {
          onUpdate: options.onUpdate,
          autoContinue: true,
          maxContinuations,
        });
        return { text: result.text, truncated: result.truncated, duration: result.duration };
      },
      healthy(): Promise<boolean> {
        return manager.isHealthy();
      },
      close(): Promise<void> {
        return session.close();
      },
    };
  }

  async close(): Promise<void> {
    await this.manager.close();
  }

  capabilities(): ProviderCapabilities {
    return {
      maxMessageChars: this.config.agent.maxMessageChars,
      supportsStreaming: true,
      supportsSystemPrompt: false,
    };
  }
}
