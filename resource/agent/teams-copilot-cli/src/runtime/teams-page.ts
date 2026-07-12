import type { CopilotConfig } from '../types.js';
import type { Page, Frame } from 'playwright-core';

export class TeamsPage {
  private page: Page;
  private config: CopilotConfig;

  constructor(page: Page, config: CopilotConfig) {
    this.page = page;
    this.config = config;
  }

  async goto(): Promise<void> {
    await this.page.goto(this.config.teamsUrl, {
      waitUntil: 'domcontentloaded',
      timeout: this.config.timeouts.pageLoad,
    });
    await this.page.waitForLoadState('domcontentloaded', { timeout: this.config.timeouts.pageLoad }).catch(() => undefined);
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
    const teamsUrl = new URL(this.config.teamsUrl);
    if (
      currentUrl.hostname === teamsUrl.hostname
      && currentUrl.pathname.replace(/\/+$/, '') === teamsUrl.pathname.replace(/\/+$/, '')
    ) {
      return false;
    }
    try {
      const loginEl = await this.page.$(this.config.selectors.loginIndicator);
      if (loginEl) return false;
    } catch {
      // Element not found = logged in
    }
    return true;
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

  async navigateToCopilot(): Promise<void> {
    const existingFrame = await this.findFrameWithInput(1000);
    if (existingFrame) return;

    await this.page.locator(this.config.selectors.copilotEntry).first().click({
      timeout: this.config.timeouts.copilotLoad,
    });
  }

  async getCopilotFrame(): Promise<Frame> {
    const frame = await this.findFrameWithInput(this.config.timeouts.copilotLoad);
    if (!frame) {
      throw Object.assign(new Error('Copilot iframe not found. Cross-origin access may require fallback.'), {
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
        if (frame === this.page.mainFrame()) continue;
        try {
          const inputCount = await frame.locator(this.config.selectors.inputArea).count();
          const sendButtonCount = await frame.locator(this.config.selectors.sendButton).count();
          if (inputCount > 0 && sendButtonCount > 0) return frame;
        } catch {
          // Cross-origin or detached frames can fail while Teams is loading.
        }
      }
      await this.page.waitForTimeout(300);
    }
    return null;
  }
}
