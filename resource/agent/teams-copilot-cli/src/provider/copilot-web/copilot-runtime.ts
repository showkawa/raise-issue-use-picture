import type {
  AppConfig,
  AskOptions,
  BrowserConfig,
  CopilotSession,
  RuntimeStatusHandler,
  StreamResult,
} from '../../types.js';
import { SessionManager } from './session-manager.js';
import { loadConfig, mergeCliFlags } from './config.js';

export class CopilotRuntime {
  private sessionManager: SessionManager;

  constructor(config?: AppConfig, onStatus?: RuntimeStatusHandler) {
    this.sessionManager = new SessionManager(config, onStatus);
  }

  async init(): Promise<void> {
    await this.sessionManager.init();
  }

  async ask(prompt: string, options?: AskOptions): Promise<StreamResult> {
    const session = await this.sessionManager.createSession();
    try {
      return await session.ask(prompt, options);
    } finally {
      await session.close();
    }
  }

  async askWithFile(
    filePath: string,
    prompt: string,
    options?: AskOptions,
  ): Promise<StreamResult> {
    const session = await this.sessionManager.createSession();
    try {
      return await session.askWithFile(filePath, prompt, options);
    } finally {
      await session.close();
    }
  }

  async createSession(): Promise<CopilotSession> {
    return this.sessionManager.createSession();
  }

  async close(): Promise<void> {
    await this.sessionManager.close();
  }
}

export async function createRuntime(
  configPath?: string,
  browserFlags?: Partial<BrowserConfig>,
  onStatus?: RuntimeStatusHandler,
): Promise<CopilotRuntime> {
  let config = loadConfig(configPath);
  if (browserFlags) {
    config = mergeCliFlags(config, browserFlags);
  }
  const runtime = new CopilotRuntime(config, onStatus);
  try {
    await runtime.init();
    return runtime;
  } catch (error) {
    await runtime.close();
    throw error;
  }
}
