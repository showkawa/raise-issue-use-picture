/**
 * runtime/opencli-adapter.ts
 * 浏览器自动化适配层 — 基于 @jackwener/opencli 的 CDPBridge。
 *
 * 致命陷阱规避：
 *  1. 禁止逐字模拟键盘 type 注入长文本 — 使用 opencli 的 type() 原生 setter 注入。
 *  2. 禁止依赖 DOM 快照提取长文本 — 必须使用注入的 MutationObserver 缓存。
 *
 * opencli 交互模型：
 *  - click(ref): ref 支持 CSS 选择器（以字母/#/[开头时走 querySelector）
 *  - type(ref, text): 使用原生 value setter，兼容 React 受控组件
 *  - evaluate(js | fn, ...args): 在浏览器上下文中执行 JS
 */

import { CDPBridge } from '@jackwener/opencli/browser/cdp';
import type { IPage } from '@jackwener/opencli/types';
import type { TeamsCopilotConfig } from './session-manager.js';
import { spawn } from 'node:child_process';

export class OpenCLIAdapter {
  private bridge: CDPBridge | null = null;
  private page: IPage | null = null;
  private edgeProcess: ReturnType<typeof spawn> | null = null;

  /**
   * 初始化浏览器实例。
   * 检查 9222 端口是否已被占用，避免重复启动 Edge。
   */
  async init(config: TeamsCopilotConfig): Promise<void> {
    const port = config.edge.debuggingPort;
    const cdpEndpoint = `http://localhost:${port}`;

    // 尝试 attach 到已有 CDP 实例
    let attached = false;
    try {
      const resp = await fetch(`${cdpEndpoint}/json/version`);
      const data = (await resp.json()) as { webSocketDebuggerUrl?: string };
      if (data.webSocketDebuggerUrl) {
        console.log('[OpenCLIAdapter] Attaching to existing Edge instance...');
        this.bridge = new CDPBridge();
        this.page = await this.bridge.connect({ cdpEndpoint });
        attached = true;
      }
    } catch {
      console.log('[OpenCLIAdapter] No existing CDP instance found, launching Edge...');
    }

    // 启动新实例（必须有头 — 企业 SSO 需要）
    if (!attached) {
      this.edgeProcess = await this.launchEdge(config);
      // 轮询等待 CDP 就绪
      await this.waitForCDP(port);
      this.bridge = new CDPBridge();
      this.page = await this.bridge.connect({ cdpEndpoint });
    }

    console.log('[OpenCLIAdapter] Browser connection established.');
  }

  /**
   * 启动 Edge 浏览器并开启 CDP 远程调试端口。
   */
  private launchEdge(config: TeamsCopilotConfig): Promise<ReturnType<typeof spawn>> {
    return new Promise((resolve, reject) => {
      const proc = spawn(
        config.edge.executablePath,
        [
          `--remote-debugging-port=${config.edge.debuggingPort}`,
          `--user-data-dir=${config.edge.userDataDir}`,
          '--no-first-run',
          '--no-default-browser-check',
        ],
        {
          detached: true,
          stdio: 'ignore',
        },
      );

      proc.on('error', (err) => {
        reject(new Error(`Failed to launch Edge: ${err.message}`));
      });

      proc.unref();
      // 给 Edge 一点启动时间后返回
      setTimeout(() => resolve(proc), 2000);
    });
  }

  /**
   * 轮询等待 CDP 端口就绪。
   */
  private async waitForCDP(port: number, timeoutMs = 30000): Promise<void> {
    const start = Date.now();
    const endpoint = `http://localhost:${port}/json/version`;

    while (Date.now() - start < timeoutMs) {
      try {
        const resp = await fetch(endpoint);
        if (resp.ok) return;
      } catch {
        // 尚未就绪
      }
      await new Promise((r) => setTimeout(r, 500));
    }

    throw new Error(`CDP port ${port} did not become ready within ${timeoutMs}ms`);
  }

  /**
   * 核心方法：通过 opencli type() 注入 Prompt 文本。
   * type() 内部使用原生 value setter + input 事件分发，兼容 React 受控组件。
   */
  async injectPrompt(text: string, inputSelector?: string): Promise<void> {
    if (!this.page) throw new Error('[OpenCLIAdapter] Page not initialized');

    const selector = inputSelector ?? '[role="textbox"][aria-label*="message"]';

    // 先聚焦输入框
    await this.page.click(selector);

    // 使用 opencli type() 注入 — 原生 setter，不会逐字触发键盘
    const pageWithType = this.page as IPage & { type(ref: string, text: string): Promise<void> };
    if (typeof pageWithType.type === 'function') {
      await pageWithType.type(selector, text);
    } else {
      // fallback: 通过 evaluate 直接设置 value
      await this.page.evaluate(
        (sel: string, txt: string) => {
          const el = document.querySelector(sel) as HTMLInputElement | HTMLTextAreaElement | null;
          if (!el) throw new Error(`Element not found: ${sel}`);
          const proto = el instanceof HTMLTextAreaElement
            ? HTMLTextAreaElement.prototype
            : HTMLInputElement.prototype;
          const nativeSetter = Object.getOwnPropertyDescriptor(proto, 'value')!;
          nativeSetter.set!.call(el, txt);
          el.dispatchEvent(new Event('input', { bubbles: true }));
          el.dispatchEvent(new Event('change', { bubbles: true }));
        },
        selector,
        text,
      );
    }

    // 等待防抖
    await new Promise((r) => setTimeout(r, 500));
  }

  /**
   * 触发发送 — 通过 evaluate 派发 Enter 键事件。
   */
  async send(): Promise<void> {
    if (!this.page) throw new Error('[OpenCLIAdapter] Page not initialized');

    const pageWithKB = this.page as IPage & { keyboard?: { press(key: string): Promise<void> } };
    if (pageWithKB.keyboard) {
      await pageWithKB.keyboard.press('Enter');
    } else {
      // fallback: 通过 evaluate 派发键盘事件
      await this.page.evaluate(() => {
        const el = document.activeElement as HTMLElement | null;
        if (!el) return;
        const opts = { key: 'Enter', code: 'Enter', bubbles: true, cancelable: true };
        el.dispatchEvent(new KeyboardEvent('keydown', opts));
        el.dispatchEvent(new KeyboardEvent('keypress', opts));
        el.dispatchEvent(new KeyboardEvent('keyup', opts));
      });
    }
  }

  /**
   * 业务级等待：等待流式输出结束。
   * 使用 evaluate 轮询注入的 window.__copilotIsStreaming 标志位。
   */
  async waitForStreamingEnd(timeout: number): Promise<void> {
    if (!this.page) throw new Error('[OpenCLIAdapter] Page not initialized');

    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      const isStreaming = await this.page.evaluate(
        () => window.__copilotIsStreaming,
      );
      if (isStreaming === false) return;
      await new Promise((r) => setTimeout(r, 500));
    }
    throw new Error('STREAMING_TIMEOUT');
  }

  /**
   * 从注入的 observer 缓存中读取当前完整文本。
   */
  async readBuffer(): Promise<string> {
    if (!this.page) throw new Error('[OpenCLIAdapter] Page not initialized');

    const text = await this.page.evaluate(
      () => window.__copilotBuffer ?? '',
    );
    return text;
  }

  /**
   * 在页面中执行 JavaScript。
   */
  async evaluate<T = unknown>(jsOrFn: string | ((...args: unknown[]) => T), ...args: unknown[]): Promise<T> {
    if (!this.page) throw new Error('[OpenCLIAdapter] Page not initialized');

    if (typeof jsOrFn === 'string') {
      return this.page.evaluate(jsOrFn) as Promise<T>;
    }
    return this.page.evaluate(jsOrFn as (...a: unknown[]) => T, ...args) as Promise<T>;
  }

  /**
   * 等待 CSS 选择器可见（通过 evaluate 轮询，兼容 opencli IPage）。
   */
  async waitForSelector(selector: string, timeout = 15000): Promise<void> {
    if (!this.page) throw new Error('[OpenCLIAdapter] Page not initialized');

    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      const found = await this.page.evaluate(
        (sel: string) => {
          const el = document.querySelector(sel);
          if (!el) return false;
          const rect = el.getBoundingClientRect();
          return rect.width > 0 && rect.height > 0;
        },
        selector,
      );
      if (found) return;
      await new Promise((r) => setTimeout(r, 300));
    }

    throw new Error(`Selector "${selector}" not visible within ${timeout}ms`);
  }

  /**
   * 点击元素（CSS 选择器或文本 ref）。
   */
  async click(ref: string): Promise<void> {
    if (!this.page) throw new Error('[OpenCLIAdapter] Page not initialized');
    await this.page.click(ref);
  }

  /** 获取当前 Page 实例（供高级操作使用）。 */
  getPage(): IPage | null {
    return this.page;
  }

  /** 关闭浏览器实例。 */
  async close(): Promise<void> {
    await this.bridge?.close();
    this.bridge = null;
    this.page = null;

    if (this.edgeProcess) {
      try {
        this.edgeProcess.kill();
      } catch {
        // 进程可能已经退出
      }
      this.edgeProcess = null;
    }
  }
}

export const adapter = new OpenCLIAdapter();
