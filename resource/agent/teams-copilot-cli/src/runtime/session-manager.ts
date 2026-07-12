import type { AppConfig, AskOptions, CopilotSession, StreamResult } from '../types.js';
import { loadConfig } from './config.js';
import {
  launchBrowser,
  connectToBrowser,
  closeBrowser,
  terminateBrowserProcess,
} from './browser-adapter.js';
import { TeamsPage } from './teams-page.js';
import { injectText } from './text-injector.js';
import { extractStream, readResponseText } from './stream-extractor.js';
import { sanitizeMarkdown } from './markdown-sanitizer.js';
import type { Browser, Frame, Page } from 'playwright-core';

const ERROR_CODES = {
  AUTH_EXPIRED: 77,
} as const;

export class SessionManager {
  private config: AppConfig;
  private browser: Browser | null = null;
  private page: Page | null = null;
  private teamsPage: TeamsPage | null = null;
  private browserPid = 0;

  constructor(config?: AppConfig) {
    this.config = config ?? loadConfig();
  }

  async init(): Promise<void> {
    const { port, pid } = await launchBrowser(this.config.browser);
    this.browserPid = pid;
    try {
      this.browser = await connectToBrowser(port);
    } catch (error) {
      terminateBrowserProcess(this.browserPid);
      this.browserPid = 0;
      throw error;
    }
    const context = this.browser.contexts()[0] ?? await this.browser.newContext();
    this.page = context.pages()[0] ?? await context.newPage();
    this.teamsPage = new TeamsPage(this.page, this.config.copilot);
  }

  async createSession(): Promise<CopilotSession> {
    if (!this.teamsPage) throw new Error('Not initialized');
    await this.teamsPage.goto();
    if (!(await this.teamsPage.isLoggedIn())) {
      process.stderr.write('Waiting up to 2 minutes for Teams sign-in...\n');
      if (!(await this.teamsPage.waitForLogin())) {
        throw Object.assign(new Error('Authentication expired. Please log in to Teams.'), {
          code: 'AUTH_EXPIRED',
          exitCode: ERROR_CODES.AUTH_EXPIRED,
        });
      }
    }
    await this.teamsPage.navigateToCopilot();
    const frame = await this.teamsPage.getCopilotFrame();
    await this.teamsPage.waitForReady(frame);

    return {
      ask: async (prompt: string, options: AskOptions = {}): Promise<StreamResult> => {
        const result = await this.askInFrame(frame, prompt, options);
        let text = result.text;
        let truncated = result.truncated;
        let duration = result.duration;
        const maxContinuations = options.maxContinuations ?? 2;

        for (let i = 0; truncated && (options.autoContinue ?? true) && i < maxContinuations; i++) {
          options.onUpdate?.('\n');
          const continuation = await this.askInFrame(
            frame,
            '请严格从你断开的地方继续输出，不要重复已输出内容。',
            options,
          );
          text = `${text}\n${continuation.text}`.trim();
          truncated = continuation.truncated;
          duration += continuation.duration;
        }

        return { text: sanitizeMarkdown(text), truncated, duration };
      },
      close: async (): Promise<void> => {
        // Session close doesn't close the browser
      },
    };
  }

  async close(): Promise<void> {
    try {
      if (this.browser) {
        await closeBrowser(this.browser);
      }
    } finally {
      this.browser = null;
      this.page = null;
      this.teamsPage = null;
      terminateBrowserProcess(this.browserPid);
      this.browserPid = 0;
    }
  }

  private async askInFrame(
    frame: Frame,
    prompt: string,
    options: AskOptions,
  ): Promise<StreamResult> {
    const baseline = await readResponseText(frame, this.config.copilot.selectors.responseContainer);
    const injection = await injectText(frame, prompt, this.config.copilot.selectors.inputArea);
    if (!injection.success) {
      throw Object.assign(new Error(`Failed to inject prompt: ${injection.error}`), {
        code: 'PROMPT_INJECTION_FAILED',
      });
    }

    await this.sendPrompt(frame);
    return extractStream(frame, this.config.copilot, {
      baseline,
      onUpdate: options.onUpdate,
    });
  }

  private async sendPrompt(frame: Frame): Promise<void> {
    try {
      await frame.locator(this.config.copilot.selectors.sendButton).first().click({ timeout: 5000 });
      return;
    } catch {
      await frame.locator(this.config.copilot.selectors.inputArea).first().click({ timeout: 5000 });
      await frame.page().keyboard.press('Enter');
    }
  }
}
