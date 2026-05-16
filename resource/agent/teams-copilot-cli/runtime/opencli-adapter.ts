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

/** CDP key event params for Input.dispatchKeyEvent */
interface CDPKeyEventParams {
  type: 'keyDown' | 'keyUp' | 'rawKeyDown' | 'char';
  key?: string;
  code?: string;
  windowsVirtualKeyCode?: number;
  modifiers?: number;
  text?: string;
  unmodifiedText?: string;
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

  /** Send a raw CDP key event via Input.dispatchKeyEvent */
  private async dispatchKeyEvent(params: CDPKeyEventParams): Promise<void> {
    await this.cdpSend('Input.dispatchKeyEvent', params as unknown as Record<string, unknown>);
  }

  /**
   * 通过 CDP Input.dispatchKeyEvent 清空 Lexical 编辑器。
   * 使用 Ctrl+A 全选 + Backspace 删除，兼容 Lexical 的 contentEditable 处理。
   */
  async clearLexicalEditor(): Promise<void> {
    // Ctrl+A: Select all
    await this.dispatchKeyEvent({
      type: 'rawKeyDown',
      key: 'Control',
      code: 'ControlLeft',
      windowsVirtualKeyCode: 17,
    });
    await this.dispatchKeyEvent({
      type: 'rawKeyDown',
      key: 'a',
      code: 'KeyA',
      windowsVirtualKeyCode: 65,
      modifiers: 2, // Ctrl
    });
    await this.dispatchKeyEvent({
      type: 'keyUp',
      key: 'a',
      code: 'KeyA',
      windowsVirtualKeyCode: 65,
      modifiers: 2,
    });
    await this.dispatchKeyEvent({
      type: 'keyUp',
      key: 'Control',
      code: 'ControlLeft',
      windowsVirtualKeyCode: 17,
    });

    // Small wait for selection
    await new Promise(r => setTimeout(r, 100));

    // Backspace: Delete selected content
    await this.dispatchKeyEvent({
      type: 'rawKeyDown',
      key: 'Backspace',
      code: 'Backspace',
      windowsVirtualKeyCode: 8,
      unmodifiedText: '\b',
    });
    await this.dispatchKeyEvent({
      type: 'keyUp',
      key: 'Backspace',
      code: 'Backspace',
      windowsVirtualKeyCode: 8,
    });

    // Small wait for Lexical to process
    await new Promise(r => setTimeout(r, 150));
  }

  /**
   * 通过 CDP Input.dispatchKeyEvent 逐字符输入文本。
   * 兼容 Lexical 编辑器——Lexical 会拦截 CDP 的 keyDown/keyUp 事件正确处理输入。
   */
  async typeViaKeyboard(text: string): Promise<void> {
    for (const char of text) {
      const keyDef = getKeyDef(char);
      // keyDown
      await this.dispatchKeyEvent({
        type: 'keyDown',
        key: char,
        code: keyDef.code,
        windowsVirtualKeyCode: keyDef.vk,
        text: char,
        unmodifiedText: char,
      });
      // char event (for text input)
      await this.dispatchKeyEvent({
        type: 'char',
        key: char,
        code: keyDef.code,
        windowsVirtualKeyCode: keyDef.vk,
        text: char,
      });
      // keyUp
      await this.dispatchKeyEvent({
        type: 'keyUp',
        key: char,
        code: keyDef.code,
        windowsVirtualKeyCode: keyDef.vk,
        text: char,
      });
      // Small delay between characters for Lexical to keep up
      await new Promise(r => setTimeout(r, 5));
    }
  }

  /**
   * 查找 Copilot 发送按钮，按优先级尝试：
   *  1. button[aria-label*="发送"]
   *  2. button[title*="发送"]
   *  3. button[aria-label*="Send"]
   * 返回按钮找到/点击状态，若都找不到则返回 null 以便 fallback。
   */
  async findSendButton(): Promise<'clicked' | 'disabled' | 'not_found'> {
    const result = await this.evaluate<string>(`(() => {
      const selectors = [
        'button[aria-label*="发送"]',
        'button[title*="发送"]',
        'button[aria-label*="Send"]',
      ];
      for (const sel of selectors) {
        const btn = document.querySelector(sel);
        if (btn) {
          if (btn.disabled) return 'disabled';
          btn.click();
          return 'clicked';
        }
      }
      return 'not_found';
    })()`);
    return result as 'clicked' | 'disabled' | 'not_found';
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
   * 使用 CDP 键盘事件清空 + 逐字符输入，100% 兼容 Lexical 编辑器。
   */
  async type(selector: string, text: string): Promise<void> {
    // 先聚焦元素
    await this.evaluate(`(() => {
      const el = document.querySelector(${JSON.stringify(selector)});
      if (!el) throw new Error('Element not found: ${selector}');
      el.focus();
    })()`);

    await new Promise(r => setTimeout(r, 300));

    // 使用 CDP 键盘事件清空
    await this.clearLexicalEditor();

    // 使用 CDP 键盘事件逐字符输入
    await this.typeViaKeyboard(text);

    // 等待 Lexical 编辑器处理所有输入事件
    await new Promise((r) => setTimeout(r, 1000));
  }

  /**
   * 点击发送按钮触发发送（优先点击按钮，fallback 到 Enter 键）。
   */
  async send(): Promise<void> {
    const status = await this.findSendButton();

    if (status === 'clicked') {
      console.log('[IFrameClient] Send button clicked successfully.');
      return;
    }

    if (status === 'disabled') {
      console.log('[IFrameClient] Send button is disabled, trying Enter key fallback.');
    } else {
      console.log('[IFrameClient] Send button not found, trying Enter key fallback.');
    }

    // Fallback: 通过 CDP 发送 Enter 键
    await this.dispatchKeyEvent({
      type: 'rawKeyDown',
      key: 'Enter',
      code: 'Enter',
      windowsVirtualKeyCode: 13,
      unmodifiedText: '\r',
    });
    await this.dispatchKeyEvent({
      type: 'char',
      key: 'Enter',
      code: 'Enter',
      windowsVirtualKeyCode: 13,
      text: '\r',
    });
    await this.dispatchKeyEvent({
      type: 'keyUp',
      key: 'Enter',
      code: 'Enter',
      windowsVirtualKeyCode: 13,
    });
  }

  /**
   * Poll-based streaming detection: directly scans iframe DOM for text changes.
   * Stops when text length unchanged for 3+ consecutive polls (3s of stability).
   */
  async waitForStreamingEnd(timeout: number): Promise<void> {
    const deadline = Date.now() + timeout;
    let lastLen = -1;
    let sameCount = 0;
    const STABILITY_THRESHOLD = 3; // Reduced from 5 to 3 for faster detection

    while (Date.now() < deadline) {
      const text = await this.pollCopilotResponse();
      const currentLen = text.length;

      // If we have substantial content and it's stable, we're done
      if (currentLen > 50 && currentLen === lastLen) {
        sameCount++;
        if (sameCount >= STABILITY_THRESHOLD) return;
      } else if (currentLen > 50 && Math.abs(currentLen - lastLen) < 10) {
        // Small changes (like punctuation) are okay, count as stable
        sameCount++;
        if (sameCount >= STABILITY_THRESHOLD) return;
      } else {
        sameCount = 0;
        lastLen = currentLen;
      }

      await new Promise((r) => setTimeout(r, 1000));
    }

    // If we have content but didn't reach stability, return anyway
    const finalText = await this.pollCopilotResponse();
    if (finalText.length > 50) return;

    throw new Error('STREAMING_TIMEOUT');
  }

  /**
   * Poll Copilot response container text directly from iframe DOM.
   * 主要读取 [role="main"] 区域，解析 "Copilot said:" 后的内容。
   */
  private async pollCopilotResponse(): Promise<string> {
    return (await this.evaluate<string>(`(() => {
      const main = document.querySelector('[role="main"]');
      if (!main) return '';

      const fullText = main.innerText || '';
      const lines = fullText.split('\\n');

      // Find the last "Copilot said:" section and extract response
      let lastCopilotStart = -1;
      for (let i = lines.length - 1; i >= 0; i--) {
        const line = lines[i].trim();
        if (line === 'Copilot' || line.startsWith('Copilot said:')) {
          lastCopilotStart = i;
          break;
        }
      }

      if (lastCopilotStart === -1) {
        return fullText.trim();
      }

      // Extract lines after "Copilot" header until next "You said:" or UI elements
      const responseLines = [];
      for (let i = lastCopilotStart + 1; i < lines.length; i++) {
        const line = lines[i].trim();

        // Stop at next user message
        if (line.startsWith('You said:')) {
          break;
        }

        // Stop at UI elements that appear after the response
        if (line.startsWith('结果计数:') ||
            line.startsWith('通过选择加号图标') ||
            line.startsWith('AI 生成的内容可能不正确') ||
            line.startsWith('向 Copilot 发送消息') ||
            line.startsWith('自动') ||
            line.startsWith('升级 Copilot') ||
            line === '提供反馈') {
          break; // Stop parsing at UI elements
        }

        // Skip empty lines and loading indicators
        if (line === '' || line === '正在生成响应') {
          continue;
        }

        responseLines.push(line);
      }

      return responseLines.join('\\n').trim() || fullText.trim();
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
// Key mapping helpers for CDP keyboard input
// ============================================================

interface KeyDef {
  code: string;
  vk: number;
}

function getKeyDef(char: string): KeyDef {
  const code = char.charCodeAt(0);

  // Letters
  if (code >= 65 && code <= 90) return { code: `Key${char}`, vk: code };
  if (code >= 97 && code <= 122) return { code: `Key${char.toUpperCase()}`, vk: code - 32 };

  // Digits
  if (code >= 48 && code <= 57) return { code: `Digit${char}`, vk: code };

  // Common symbols and punctuation
  const symbolMap: Record<string, { code: string; vk: number }> = {
    ' ':  { code: 'Space',        vk: 0x20 },
    '!':  { code: 'Digit1',       vk: 0x31 },
    '"':  { code: 'Quote',        vk: 0xDE },
    '#':  { code: 'Digit3',       vk: 0x33 },
    '$':  { code: 'Digit4',       vk: 0x34 },
    '%':  { code: 'Digit5',       vk: 0x35 },
    '&':  { code: 'Digit7',       vk: 0x37 },
    "'":  { code: 'Quote',        vk: 0xDE },
    '(':  { code: 'Digit9',       vk: 0x39 },
    ')':  { code: 'Digit0',       vk: 0x30 },
    '*':  { code: 'Digit8',       vk: 0x38 },
    '+':  { code: 'Equal',        vk: 0xBB },
    ',':  { code: 'Comma',        vk: 0xBC },
    '-':  { code: 'Minus',        vk: 0xBD },
    '.':  { code: 'Period',       vk: 0xBE },
    '/':  { code: 'Slash',        vk: 0xBF },
    ':':  { code: 'Semicolon',    vk: 0xBA },
    ';':  { code: 'Semicolon',    vk: 0xBA },
    '<':  { code: 'Comma',        vk: 0xBC },
    '=':  { code: 'Equal',        vk: 0xBB },
    '>':  { code: 'Period',       vk: 0xBE },
    '?':  { code: 'Slash',        vk: 0xBF },
    '@':  { code: 'Digit2',       vk: 0x32 },
    '[':  { code: 'BracketLeft',  vk: 0xDB },
    '\\': { code: 'Backslash',    vk: 0xDC },
    ']':  { code: 'BracketRight', vk: 0xDD },
    '^':  { code: 'Digit6',       vk: 0x36 },
    '_':  { code: 'Minus',        vk: 0xBD },
    '`':  { code: 'Backquote',    vk: 0xC0 },
    '{':  { code: 'BracketLeft',  vk: 0xDB },
    '|':  { code: 'Backslash',    vk: 0xDC },
    '}':  { code: 'BracketRight', vk: 0xDD },
    '~':  { code: 'Backquote',    vk: 0xC0 },
  };

  if (symbolMap[char]) return symbolMap[char];

  // Fallback for unknown characters: use key char itself and common VK
  return { code: `Key${char.toUpperCase()}`, vk: code || 0xBF };
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
   * 查找发送按钮，按优先级尝试：
   *  1. button[aria-label*="发送"]
   *  2. button[title*="发送"]
   *  3. button[aria-label*="Send"]
   * 返回点击结果；用于主页面（非 iframe 模式）。
   */
  async findSendButton(): Promise<'clicked' | 'disabled' | 'not_found'> {
    if (this.iframe) {
      return this.iframe.findSendButton();
    }

    if (!this.page) throw new Error('[OpenCLIAdapter] Page not initialized');

    const result = await this.page.evaluate(() => {
      const selectors = [
        'button[aria-label*="发送"]',
        'button[title*="发送"]',
        'button[aria-label*="Send"]',
      ];
      for (const sel of selectors) {
        const btn = document.querySelector(sel) as HTMLButtonElement | null;
        if (btn) {
          if (btn.disabled) return 'disabled';
          btn.click();
          return 'clicked';
        }
      }
      return 'not_found';
    });

    return result as 'clicked' | 'disabled' | 'not_found';
  }

  /**
   * 核心方法：注入 Prompt 文本到 Copilot 输入框。
   * 优先使用 iframe 连接（contentEditable span），fallback 到主页面原生 value setter。
   */
  async injectPrompt(text: string, inputSelector?: string): Promise<void> {
    if (this.iframe) {
      const selector = inputSelector ?? '[contenteditable="true"], [role="textbox"]';
      // 等待 iframe 中的输入框就绪
      await this.iframeWaitForSelector(selector, 15000);
      // IFrameClient.type() 内部使用 clearLexicalEditor + typeViaKeyboard
      await this.iframe.type(selector, text);
      return;
    }

    if (!this.page) throw new Error('[OpenCLIAdapter] Page not initialized');

    const selector = inputSelector ?? '[contenteditable="true"], [role="textbox"], textarea[aria-label*="消息"]';

    // 先聚焦输入框
    await this.page.click(selector);

    // 使用 opencli type() 注入
    const pageWithType = this.page as IPage & { type(ref: string, text: string): Promise<void> };
    if (typeof pageWithType.type === 'function') {
      await pageWithType.type(selector, text);
    } else {
      // fallback: 通过 evaluate 使用原生 setter 注入
      await this.page.evaluate(
        (sel: string, txt: string) => {
          const el = document.querySelector(sel) as HTMLElement | null;
          if (!el) throw new Error(`Element not found: ${sel}`);

          el.focus();
          el.textContent = '';
          el.dispatchEvent(new Event('input', { bubbles: true }));

          // 处理 contenteditable 元素
          if (el.isContentEditable) {
            el.textContent = txt;
            el.dispatchEvent(new InputEvent('input', {
              bubbles: true,
              cancelable: true,
              inputType: 'insertFromPaste',
              data: txt,
            }));
          } else {
            // 处理 input/textarea 元素
            const proto = el instanceof HTMLTextAreaElement
              ? HTMLTextAreaElement.prototype
              : HTMLInputElement.prototype;
            const nativeSetter = Object.getOwnPropertyDescriptor(proto, 'value')!;
            nativeSetter.set!.call(el, txt);
          }

          el.dispatchEvent(new Event('change', { bubbles: true }));
        },
        selector,
        text,
      );
    }

    // 等待防抖和 Lexical 处理
    await new Promise((r) => setTimeout(r, 1500));
  }

  /**
   * 触发发送 — 优先使用 findSendButton() 查找发送按钮，fallback 到 Enter 键。
   * iframe 模式下使用 IFrameClient.send() 操作。
   */
  async send(): Promise<void> {
    if (this.iframe) {
      await this.iframe.send();
      return;
    }

    if (!this.page) throw new Error('[OpenCLIAdapter] Page not initialized');

    const status = await this.findSendButton();

    if (status === 'clicked') {
      console.log('[OpenCLIAdapter] Send button clicked successfully.');
      return;
    }

    console.log(`[OpenCLIAdapter] Send button ${status}, falling back to Enter key.`);

    // Fallback: 通过 keyboard 或 evaluate 派发 Enter 键
    const pageWithKB = this.page as IPage & { keyboard?: { press(key: string): Promise<void> } };
    if (pageWithKB.keyboard) {
      await pageWithKB.keyboard.press('Enter');
    } else {
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
   * 使用直接 DOM 轮询（与 iframe 路径一致）。
   * 带重试机制：失败后重试最多 3 次。
   */
  async waitForStreamingEnd(timeout: number, maxRetries = 3): Promise<void> {
    let lastError: Error | undefined;

    for (let attempt = 0; attempt <= maxRetries; attempt++) {
      try {
        if (this.iframe) {
          await this.iframe.waitForStreamingEnd(timeout);
        } else if (this.page) {
          await this.waitForStreamingEndPage(timeout);
        } else {
          throw new Error('[OpenCLIAdapter] Page not initialized');
        }
        return; // Success
      } catch (err) {
        lastError = err instanceof Error ? err : new Error(String(err));
        if (attempt < maxRetries) {
          console.log(`[OpenCLIAdapter] Streaming check failed (attempt ${attempt + 1}/${maxRetries + 1}), retrying...`);
          await new Promise(r => setTimeout(r, 2000));
        }
      }
    }

    throw lastError ?? new Error('STREAMING_TIMEOUT');
  }

  /**
   * 主页面的 waitForStreamingEnd 实现（与 iframe 路径一致的轮询策略）。
   */
  private async waitForStreamingEndPage(timeout: number): Promise<void> {
    if (!this.page) throw new Error('[OpenCLIAdapter] Page not initialized');

    const deadline = Date.now() + timeout;
    let lastLen = -1;
    let sameCount = 0;
    const STABILITY_THRESHOLD = 3;

    while (Date.now() < deadline) {
      const text = await this.page.evaluate(() => {
        const main = document.querySelector('[role="main"]');
        if (!main) return '';
        return (main as HTMLElement).innerText || '';
      }) as string;

      const currentLen = text.length;
      if (currentLen > 50 && currentLen === lastLen) {
        sameCount++;
        if (sameCount >= STABILITY_THRESHOLD) return;
      } else if (currentLen > 50 && Math.abs(currentLen - lastLen) < 10) {
        sameCount++;
        if (sameCount >= STABILITY_THRESHOLD) return;
      } else {
        sameCount = 0;
        lastLen = currentLen;
      }

      await new Promise((r) => setTimeout(r, 1000));
    }

    // If we have content but didn't reach stability, return anyway
    const finalText = await this.page.evaluate(() => {
      const main = document.querySelector('[role="main"]');
      if (!main) return '';
      return (main as HTMLElement).innerText || '';
    }) as string;
    if (finalText.length > 50) return;

    throw new Error('STREAMING_TIMEOUT');
  }

  /**
   * 从 DOM 中读取当前完整文本（与 iframe 路径一致）。
   */
  async readBuffer(): Promise<string> {
    if (this.iframe) {
      return this.iframe.readBuffer();
    }

    if (!this.page) throw new Error('[OpenCLIAdapter] Page not initialized');

    const text = await this.page.evaluate(() => {
      const main = document.querySelector('[role="main"]');
      if (!main) return '';
      return (main as HTMLElement).innerText || '';
    }) as string;
    return text ?? '';
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
