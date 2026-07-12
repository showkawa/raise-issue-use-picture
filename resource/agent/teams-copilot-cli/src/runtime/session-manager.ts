import type { AppConfig, AskOptions, CopilotSession, StreamResult } from '../types.js';
import { loadConfig } from './config.js';
import {
  launchBrowser,
  connectToBrowser,
  closeBrowser,
  terminateBrowserProcess,
} from './browser-adapter.js';
import { CopilotPage } from './copilot-page.js';
import { injectText } from './text-injector.js';
import { extractStream, readResponseText } from './stream-extractor.js';
import { createSignalRStream } from './signalr-stream.js';
import {
  askWithBrowserApi,
  clearBrowserApiTemplate,
  installBrowserApiBridge,
} from './browser-api-bridge.js';
import { uploadCodeFile } from './code-file-uploader.js';
import { sanitizeMarkdown } from './markdown-sanitizer.js';
import type { Browser, Frame, Page } from 'playwright-core';

const ERROR_CODES = {
  AUTH_EXPIRED: 77,
} as const;

export class SessionManager {
  private config: AppConfig;
  private browser: Browser | null = null;
  private page: Page | null = null;
  private copilotPage: CopilotPage | null = null;
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
    const target = new URL(this.config.copilot.copilotUrl);
    this.page = context.pages().find((page) => {
      try {
        const url = new URL(page.url());
        const targetPath = target.pathname.replace(/\/+$/, '');
        const currentPath = url.pathname.replace(/\/+$/, '');
        return url.origin === target.origin
          && (currentPath === targetPath || currentPath.startsWith(`${targetPath}/`));
      } catch {
        return false;
      }
    }) ?? context.pages()[0] ?? await context.newPage();
    this.copilotPage = new CopilotPage(this.page, this.config.copilot);
  }

  async createSession(): Promise<CopilotSession> {
    if (!this.copilotPage) throw new Error('Not initialized');
    await this.copilotPage.goto();
    if (!(await this.copilotPage.isLoggedIn())) {
      process.stderr.write('Waiting up to 2 minutes for Microsoft 365 Copilot sign-in...\n');
      if (!(await this.copilotPage.waitForLogin())) {
        const authError = this.copilotPage.getAuthError();
        const message = authError
          ? `Microsoft authentication failed: ${authError}`
          : 'Authentication expired. Please log in to Microsoft 365 Copilot.';
        throw Object.assign(new Error(message), {
          code: 'AUTH_EXPIRED',
          exitCode: ERROR_CODES.AUTH_EXPIRED,
        });
      }
    }
    const frame = await this.copilotPage.getChatFrame();
    await this.copilotPage.waitForReady(frame);
    await installBrowserApiBridge(this.page ?? frame.page());

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
      askWithFile: async (
        filePath: string,
        prompt: string,
        options: AskOptions = {},
      ): Promise<StreamResult> => {
        const page = this.page ?? frame.page();
        const uploaded = await uploadCodeFile(
          page,
          filePath,
          this.config.copilot.timeouts.streaming,
          this.config.copilot.selectors.fileInput,
        );
        const reviewPrompt = uploaded.aliased
          ? `${prompt}\n\n附件 "${uploaded.uploadName}" 的原始文件名是 "${uploaded.originalName}"。`
          : prompt;
        try {
          const result = await this.askInFrame(frame, reviewPrompt, options, true);
          return {
            ...result,
            text: sanitizeMarkdown(result.text),
          };
        } finally {
          await clearBrowserApiTemplate(page);
        }
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
      this.copilotPage = null;
      terminateBrowserProcess(this.browserPid);
      this.browserPid = 0;
    }
  }

  private async askInFrame(
    frame: Frame,
    prompt: string,
    options: AskOptions,
    forceDom = false,
  ): Promise<StreamResult> {
    if (!forceDom && this.config.copilot.requestMode !== 'dom') {
      try {
        const browserApiResult = await askWithBrowserApi(
          this.page ?? frame.page(),
          prompt,
          this.config.copilot.timeouts.streaming,
          this.config.copilot.timeouts.pollingInterval,
          options.onUpdate,
        );
        if (browserApiResult) return browserApiResult;
      } catch (error) {
        if (this.config.copilot.requestMode === 'browser-api') throw error;
      }
    }

    const baseline = await readResponseText(frame, this.config.copilot.selectors.responseContainer);
    const signalRStream = this.config.copilot.responseMode === 'dom'
      ? null
      : await createSignalRStream(this.page ?? frame.page(), this.config.copilot, options.onUpdate);
    const injection = await injectText(frame, prompt, this.config.copilot.selectors.inputArea);
    if (!injection.success) {
      await signalRStream?.dispose();
      throw Object.assign(new Error(`Failed to inject prompt: ${injection.error}`), {
        code: 'PROMPT_INJECTION_FAILED',
      });
    }

    await this.sendPrompt(frame);
    try {
      if (signalRStream) return await signalRStream.wait();
      return await this.extractFromDom(frame, baseline, options);
    } catch (error) {
      if (this.config.copilot.responseMode === 'signalr') throw error;
      return this.extractFromDom(frame, baseline, options);
    } finally {
      await signalRStream?.dispose();
    }
  }

  private extractFromDom(
    frame: Frame,
    baseline: string,
    options: AskOptions,
  ): Promise<StreamResult> {
    return extractStream(frame, this.config.copilot, {
      baseline,
      onUpdate: this.config.copilot.responseMode === 'dom' ? options.onUpdate : undefined,
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
