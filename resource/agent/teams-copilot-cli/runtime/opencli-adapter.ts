/**
 * runtime/opencli-adapter.ts
 * 浏览器自动化适配层 — 基于 @jackwener/opencli 的 CDPBridge。
 *
 * 致命陷阱规避：
 *  1. 禁止逐字模拟键盘 type 注入长文本 — 使用原生 setter 注入。
 *  2. 禁止依赖 DOM 快照提取长文本 — 必须使用注入的 MutationObserver 缓存。
 *  3. Copilot 输入框在跨域 iframe 内 — 需通过 IFrameClient 的 CDP WebSocket 单独连接。
 *
 * opencli 交互模型（主页面）：
 *  - click(ref): ref 支持 CSS 选择器（以字母/#/[开头时走 querySelector）
 *  - type(ref, text): 使用原生 value setter，兼容 React 受控组件
 *  - evaluate(js | fn, ...args): 在浏览器上下文中执行 JS
 *
 * IFrameClient（Copilot iframe）:
 *  - 通过 raw WebSocket CDP 协议直连 iframe target
 *  - 提供 evaluate / type / send / waitForStreamingEnd
 */

import { CDPBridge } from '@jackwener/opencli/browser/cdp';
import type { IPage } from '@jackwener/opencli/types';
import type { TeamsCopilotConfig } from './session-manager.js';
import { spawn } from 'node:child_process';

// ============================================================
// IFrameClient — 跨域 Copilot iframe 的 CDP WebSocket 客户端
// ============================================================

interface CDPRequest {
  id: number;
  method: string;
  params?: Record<string, unknown>;
}

interface CDPResponse {
  id?: number;
  result?: Record<string, unknown>;
  error?: { message: string };
}

class IFrameClient {
  private ws: WebSocket | null = null;
  private msgId = 0;
  private pending = new Map<number, { resolve: (v: unknown) => void; reject: (e: Error) => void }>();

  async connect(cdpPort: number, timeoutMs = 60000): Promise<void> {
    const deadline = Date.now() + timeoutMs;

    while (Date.now() < deadline) {
      const res = await fetch(`http://localhost:${cdpPort}/json/list`);
      const targets = (await res.json()) as Array<{
        id: string; type: string; url: string;
        webSocketDebuggerUrl: string;
      }>;

      const iframeTarget = targets.find(
        (t) =>
          t.url?.includes('outlook.office.com/hosted/semanticoverview') ||
          t.url?.includes('outlook.office.com/hosted/copilot'),
      );

      if (iframeTarget) {
        console.log(`[IFrameClient] Connecting to iframe: ${iframeTarget.url}`);

        this.ws = new WebSocket(iframeTarget.webSocketDebuggerUrl);

        await new Promise<void>((resolve, reject) => {
          this.ws!.onopen = () => resolve();
          this.ws!.onerror = (e) => reject(new Error(`WebSocket error: ${JSON.stringify(e)}`));
          setTimeout(() => reject(new Error('WebSocket connection timeout')), 5000);
        });

        // setup message handling
        this.ws!.onmessage = (event) => {
          const msg = JSON.parse(event.data as string) as CDPResponse;
          if (msg.id !== undefined && this.pending.has(msg.id)) {
            const { resolve, reject } = this.pending.get(msg.id)!;
            this.pending.delete(msg.id);
            if (msg.error) {
              reject(new Error(msg.error.message));
            } else {
              resolve(msg.result ?? {});
            }
          }
        };

        this.ws!.onclose = () => {
          for (const [, { reject }] of this.pending) {
            reject(new Error('WebSocket closed'));
          }
          this.pending.clear();
        };

        await this.cdpSend('Runtime.enable');
        console.log('[IFrameClient] Connected to Copilot iframe.');
        return;
      }

      // iframe not found, wait and retry
      await new Promise(r => setTimeout(r, 2000));
    }

    throw new Error(
      'Copilot iframe not found within timeout. Make sure the Copilot panel is open.',
    );
  }

  private cdpSend(method: string, params?: Record<string, unknown>): Promise<unknown> {
    return new Promise((resolve, reject) => {
      const id = ++this.msgId;
      const payload: CDPRequest = { id, method };
      if (params) payload.params = params;
      this.pending.set(id, { resolve, reject });
      this.ws!.send(JSON.stringify(payload));
    });
  }

  async evaluate<T = unknown>(expression: string): Promise<T> {
    const res = (await this.cdpSend('Runtime.evaluate', {
      expression,
      returnByValue: true,
    })) as { result?: { value: T } };
    return res.result?.value as T;
  }

  /**
   * 在 contentEditable 元素中注入文本（Copilot 输入框是 span[contenteditable]）。
   */
  async type(selector: string, text: string): Promise<void> {
    await this.evaluate(`(() => {
      const el = document.querySelector(${JSON.stringify(selector)});
      if (!el) throw new Error('Element not found: ${selector}');
      el.focus();
      // contentEditable 元素：设置 textContent 并派发 input 事件
      el.textContent = ${JSON.stringify(text)};
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
    })()`);
  }

  /**
   * 在 contentEditable 元素中派发 Enter 键事件触发发送。
   */
  async send(): Promise<void> {
    await this.evaluate(`(() => {
      const el = document.activeElement;
      if (!el) return;
      const opts = { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true };
      el.dispatchEvent(new KeyboardEvent('keydown', opts));
      el.dispatchEvent(new KeyboardEvent('keypress', opts));
      el.dispatchEvent(new KeyboardEvent('keyup', opts));
    })()`);
  }

  /**
   * Poll-based streaming detection: directly scans iframe DOM for text changes.
   * Stops when text length unchanged for 5+ consecutive polls (5s of stability).
   */
  async waitForStreamingEnd(timeout: number): Promise<void> {
    const deadline = Date.now() + timeout;
    let lastLen = -1;
    let sameCount = 0;

    while (Date.now() < deadline) {
      const text = await this.pollCopilotResponse();
      const currentLen = text.length;

      if (currentLen > 0 && currentLen === lastLen) {
        sameCount++;
        if (sameCount >= 5) return; // stable for 5 polls = done
      } else {
        sameCount = 0;
        lastLen = currentLen;
      }

      await new Promise((r) => setTimeout(r, 1000));
    }
    throw new Error('STREAMING_TIMEOUT');
  }

  /**
   * Poll Copilot response container text directly from iframe DOM.
   * 只读对话区域，不回退到 document.body（噪音太多）。
   */
  private async pollCopilotResponse(): Promise<string> {
    return (await this.evaluate<string>(`(() => {
      // Try specific Copilot response containers (Outlook/M365 Copilot iframe).
      // Order matters: most-specific first, fallback to main area LAST.
      // AVOID document.body — it includes sidebar, header, timestamps.
      const sel = document.querySelector(
        '[data-content="ai-message"]:last-child, ' +
        '.ac-container:last-child, ' +
        '[role="log"]:last-child, ' +
        '[aria-live="polite"]'
      );
      if (sel) return sel.innerText || '';

      // Fallback: entire main role area (excludes sidebars)
      const main = document.querySelector('[role="main"]');
      if (main) return main.innerText?.trim() || '';
      return '';
    })()`)) ?? '';
  }

  async readBuffer(): Promise<string> {
    return this.pollCopilotResponse();
  }

  close(): void {
    this.ws?.close();
    this.ws = null;
    this.pending.clear();
  }
}

// ============================================================
// OpenCLIAdapter
// ============================================================

export class OpenCLIAdapter {
  private bridge: CDPBridge | null = null;
  private page: IPage | null = null;
  private edgeProcess: ReturnType<typeof spawn> | null = null;
  private iframe: IFrameClient | null = null;
  private port = 0;

  /**
   * 初始化浏览器实例。
   * 检查 9222 端口是否已被占用，避免重复启动 Edge。
   */
  async init(config: TeamsCopilotConfig): Promise<void> {
    this.port = config.edge.debuggingPort;
    const cdpEndpoint = `http://localhost:${this.port}`;

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
      await this.waitForCDP(this.port);
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
   * 连接到 Copilot 跨域 iframe（outlook.office.com）。
   * 必须在 Copilot 面板打开后调用，否则找不到 iframe target。
   */
  async connectToCopilotIframe(): Promise<void> {
    if (this.iframe) {
      this.iframe.close();
    }
    this.iframe = new IFrameClient();
    await this.iframe.connect(this.port);
  }

  /** 是否已连接到 Copilot iframe */
  hasIframe(): boolean {
    return this.iframe !== null;
  }

  /**
   * 获取当前操作目标 — 优先使用 iframe（Copilot 输入/输出），否则用主页面。
   */
  private target(): 'iframe' | 'page' {
    return this.iframe ? 'iframe' : 'page';
  }

  /**
   * 核心方法：注入 Prompt 文本到 Copilot 输入框。
   * 优先使用 iframe 连接（contentEditable span），fallback 到主页面原生 value setter。
   */
  async injectPrompt(text: string, inputSelector?: string): Promise<void> {
    if (this.iframe) {
      const selector = inputSelector ?? '[role="textbox"][contenteditable="true"]';
      // 等待 iframe 中的输入框就绪
      await this.iframeWaitForSelector(selector, 15000);
      await this.iframe.type(selector, text);
      await new Promise((r) => setTimeout(r, 500));
      return;
    }

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
   * iframe 模式下使用 IFrameClient.send() 操作 contentEditable 元素。
   */
  async send(): Promise<void> {
    if (this.iframe) {
      await this.iframe.send();
      return;
    }

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
    if (this.iframe) {
      await this.iframe.waitForStreamingEnd(timeout);
      return;
    }

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
    if (this.iframe) {
      return this.iframe.readBuffer();
    }

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
    if (this.iframe) {
      return this.iframeWaitForSelector(selector, timeout);
    }

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

  /** iframe 中的 waitForSelector（通过 CDP evaluate 轮询） */
  private async iframeWaitForSelector(selector: string, timeout = 15000): Promise<void> {
    if (!this.iframe) throw new Error('IFrame not connected');
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      const found = await this.iframe.evaluate<boolean>(
        `(function(){
          const el = document.querySelector(${JSON.stringify(selector)});
          if (!el) return false;
          const rect = el.getBoundingClientRect();
          return rect.width > 0 && rect.height > 0;
        })()`,
      );
      if (found) return;
      await new Promise((r) => setTimeout(r, 300));
    }
    throw new Error(`IFrame selector "${selector}" not visible within ${timeout}ms`);
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

  /** 获取 Copilot iframe 客户端（供 observer 注入等高级操作）。 */
  getIFrameClient(): IFrameClient | null {
    return this.iframe;
  }

  /** 关闭浏览器实例。 */
  async close(): Promise<void> {
    this.iframe?.close();
    this.iframe = null;

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
