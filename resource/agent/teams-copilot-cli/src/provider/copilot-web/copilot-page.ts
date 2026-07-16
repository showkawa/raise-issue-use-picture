import type { CopilotConfig } from '../../types.js';
import type { Page, Frame } from 'playwright-core';

export class CopilotPage {
  private page: Page;
  private config: CopilotConfig;

  constructor(page: Page, config: CopilotConfig) {
    this.page = page;
    this.config = config;
  }

  async goto(): Promise<void> {
    if (this.isCopilotUrl(this.page.url())) {
      await this.page.waitForLoadState('domcontentloaded', {
        timeout: this.config.timeouts.pageLoad,
      }).catch(() => undefined);
      return;
    }

    await this.page.goto(this.config.copilotUrl, {
      waitUntil: 'domcontentloaded',
      timeout: this.config.timeouts.pageLoad,
    });
    await this.page.waitForLoadState('domcontentloaded', {
      timeout: this.config.timeouts.pageLoad,
    }).catch(() => undefined);
  }

  async isLoggedIn(): Promise<boolean> {
    await this.page.waitForTimeout(1000);
    const currentUrl = new URL(this.page.url());
    if (this.getAuthError()) return false;
    const hostname = currentUrl.hostname.toLowerCase();
    if (
      hostname === 'login.microsoftonline.com'
      || hostname.endsWith('.login.microsoftonline.com')
      || hostname === 'login.live.com'
      || currentUrl.pathname.toLowerCase().includes('signin')
    ) {
      return false;
    }

    try {
      if (await this.page.locator(this.config.selectors.loginIndicator).first().isVisible()) {
        return false;
      }
    } catch {
      return false;
    }

    return this.isCopilotUrl(currentUrl.toString());
  }

  async waitForLogin(timeout = 120000): Promise<boolean> {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      if (this.getAuthError()) return false;
      if (await this.isLoggedIn()) return true;
      await this.page.waitForTimeout(1000);
    }
    return false;
  }

  getAuthError(): string | null {
    const currentUrl = new URL(this.page.url());
    const fragment = new URLSearchParams(currentUrl.hash.slice(1));
    return currentUrl.searchParams.get('error_description')
      ?? fragment.get('error_description')
      ?? currentUrl.searchParams.get('error')
      ?? fragment.get('error');
  }

  async getChatFrame(): Promise<Frame> {
    const frame = await this.findFrameWithInput(this.config.timeouts.copilotLoad);
    if (!frame) {
      throw Object.assign(new Error('Microsoft 365 Copilot chat input not found.'), {
        code: 'IFRAME_ACCESS_FAILED',
      });
    }
    return frame;
  }

  async waitForReady(frame: Frame): Promise<void> {
    await frame.locator(this.config.selectors.inputArea).first().waitFor({
      state: 'visible',
      timeout: this.config.timeouts.copilotLoad,
    });
  }

  private async findFrameWithInput(timeout: number): Promise<Frame | null> {
    const deadline = Date.now() + timeout;
    while (Date.now() <= deadline) {
      for (const frame of this.page.frames()) {
        try {
          const input = frame.locator(this.config.selectors.inputArea).first();
          if (await input.isVisible()) return frame;
        } catch {
          // Detached frames can fail while Microsoft 365 Copilot is loading.
        }
      }
      await this.page.waitForTimeout(300);
    }
    return null;
  }

  private isCopilotUrl(value: string): boolean {
    try {
      const target = new URL(this.config.copilotUrl);
      const current = new URL(value);
      const targetPath = target.pathname.replace(/\/+$/, '');
      const currentPath = current.pathname.replace(/\/+$/, '');
      return current.origin === target.origin
        && (currentPath === targetPath || currentPath.startsWith(`${targetPath}/`));
    } catch {
      return false;
    }
  }
}
