/**
 * runtime/copilot-runtime.ts
 * 业务运行时 — 编排登录态检测、Prompt 注入、流式获取、截断自动续写。
 *
 * 基于 @jackwener/opencli 的 IPage 接口（非 Playwright）。
 *   - goto 只接受 { waitUntil?: 'load' | 'none'; settleMs?: number }
 *   - click/type 的 ref 支持 CSS 选择器
 *   - url() 通过 evaluate 获取
 *   - waitFor({ selector, timeout }) 替代 waitForSelector
 */

import { OpenCLIAdapter } from './opencli-adapter.js';
import {
  loadConfig,
  isAuthenticated,
  mapErrorCode,
  type TeamsCopilotConfig,
} from './session-manager.js';

export class CopilotRuntime {
  private adapter: OpenCLIAdapter;
  private config!: TeamsCopilotConfig;

  constructor(adapterOverride?: OpenCLIAdapter) {
    this.adapter = adapterOverride ?? new OpenCLIAdapter();
  }

  /**
   * 初始化：加载配置并启动浏览器。
   */
  async init(configPath?: string): Promise<void> {
    this.config = loadConfig(configPath);
    await this.adapter.init(this.config);
  }

  /**
   * 确保 Teams 会话有效：
   *  1. 导航到 Teams
   *  2. 检测是否被重定向到登录页
   *  （Observer 注入推迟到 Copilot iframe 连接之后）
   */
  async ensureSession(): Promise<void> {
    const page = this.adapter.getPage();
    if (!page) throw new Error('[CopilotRuntime] Page not initialized');

    // 检查当前 URL，如果已在 Teams 则跳过导航
    const currentUrl = (await page.evaluate(() => window.location.href)) as string;
    if (!currentUrl.includes('teams.')) {
      await page.goto(this.config.copilot.url, { waitUntil: 'load', settleMs: 3000 });
    }

    // 获取实际 URL（可能被重定向）
    const url = (await page.evaluate(() => window.location.href)) as string;
    if (!isAuthenticated(url)) {
      throw new Error(
        'AUTH_EXPIRED: 请在弹出的浏览器中手动完成 MFA 登录，然后重新运行命令。',
      );
    }
  }

  /**
   * 导航到 Copilot 并注入 Prompt。
   * 1. 检测 Copilot 按钮 → 点击打开 Copilot 面板
   * 2. 连接 Copilot 跨域 iframe（outlook.office.com）
   * 3. 在 iframe 中注入 MutationObserver 监听脚本
   * 4. 定位 iframe 中的 contentEditable 输入框并注入 Prompt
   */
  async triggerCopilotAndInput(prompt: string): Promise<void> {
    const page = this.adapter.getPage();
    if (!page) throw new Error('[CopilotRuntime] Page not initialized');

    // 等待页面稳定
    await new Promise(r => setTimeout(r, 2000));

    // 点击左侧菜单栏 Copilot 按钮打开对话面板（iframe）。
    // 即使页面已经显示 Copilot 标题，也必须点击才能触发 iframe 加载。
    console.log('[CopilotRuntime] Clicking Copilot sidebar button...');
    try {
      await this.adapter.click('button[aria-label*="Copilot (Ctrl+Shift+6)"]');
    } catch {
      try {
        await this.adapter.click('button[aria-label*="Copilot"]');
      } catch {
        throw new Error(
          'COPILOT_UNAVAILABLE: 无法找到左侧菜单栏的 Copilot 按钮。请手动点击后重试。',
        );
      }
    }

    // 等待 iframe 加载
    console.log('[CopilotRuntime] Waiting for Copilot iframe...');
    await new Promise(r => setTimeout(r, 5000));

    // 连接到 Copilot 跨域 iframe
    await this.adapter.connectToCopilotIframe();

    // Copilot iframe 中的输入框选择器（contentEditable span）
    const iframeInputSelector = this.config.copilot.inputSelector;

    // 等待 iframe 中的输入框可用
    await this.adapter.waitForSelector(iframeInputSelector, 15000);

    // 注入 Prompt（优先使用 iframe）
    await this.adapter.injectPrompt(prompt, iframeInputSelector);

    // 发送
    console.log('[CopilotRuntime] Prompt sent, waiting for response...');
    await this.adapter.send();
  }

  /**
   * 流式获取结果，包含截断检测与自动续写。
   *
   * 流程：
   *  1. 等待流式输出结束（带重试机制，最多 3 次）
   *  2. 防抖确认文本稳定（2 个循环）
   *  3. 检测截断 → 自动发送续写 Prompt
   *  4. 清洗 Teams 添加的废话（前缀寒暄/后缀追问）
   *  5. 超时后返回已获取的部分内容而不是直接抛错
   */
  async fetchResult(): Promise<string> {
    let finalText = '';
    let lastLength = 0;
    let stableCount = 0;

    const timeout = this.config.copilot.timeout;
    const MAX_LOOP_ITERATIONS = 20;
    let loopCount = 0;

    let truncationRetries = 0;
    const MAX_TRUNCATION_RETRIES = 2;

    while (loopCount < MAX_LOOP_ITERATIONS) {
      loopCount++;
      try {
        // 带重试的 waitForStreamingEnd（内部重试最多 3 次）
        await this.adapter.waitForStreamingEnd(timeout, 3);

        const currentText = await this.adapter.readBuffer();

        // If we got empty text, retry once
        if (currentText.length === 0) {
          if (loopCount === 1) {
            lastLength = 0;
            continue;
          }
          break; // Give up after first retry
        }

        // 防抖：判断文本是否真正停止变化
        if (currentText.length === lastLength && currentText.length > 0) {
          stableCount++;
        } else {
          stableCount = 0;
        }

        if (stableCount >= 2 && currentText.length > 0) {
          finalText = currentText;

          // 检测截断：末尾无标点，或代码块未闭合
          const trimmedText = finalText.trim();
          const isTruncated =
            truncationRetries < MAX_TRUNCATION_RETRIES &&
            (!/[.?!。？！\n`]{1,2}$/.test(trimmedText) ||
            (trimmedText.split('```').length % 2 === 0));

          if (isTruncated) {
            truncationRetries++;
            console.log(`[CopilotRuntime] Detected truncation (retry ${truncationRetries}/${MAX_TRUNCATION_RETRIES}), sending continue prompt...`);
            await this.adapter.injectPrompt(
              '请严格从你断开的地方继续输出，不要重复已输出的内容，不要任何前缀。',
              this.config.copilot.inputSelector,
            );
            await this.adapter.send();
            lastLength = 0;
            stableCount = 0;
            continue;
          }

          break; // 正常结束
        }

        lastLength = currentText.length;
      } catch (err) {
        // 响应超时后返回已获取的部分内容而不是直接抛错
        const partialText = await this.adapter.readBuffer().catch(() => '');
        if (partialText.length > 50) {
          console.log('[CopilotRuntime] Streaming timeout but got partial response, returning it.');
          finalText = partialText;
          break;
        }
        if (finalText.length > 50) {
          console.log('[CopilotRuntime] Timeout but got partial response from previous loop, returning it.');
          break;
        }
        // Only throw if we truly have nothing
        throw new Error(
          `STREAMING_TIMEOUT: 生成超时（${timeout}ms），请检查网络或 Copilot 状态。` +
            (err instanceof Error ? `\n原始错误: ${err.message}` : ''),
        );
      }
    }

    if (loopCount >= MAX_LOOP_ITERATIONS && finalText.length === 0) {
      throw new Error('STREAMING_TIMEOUT: 达到最大循环次数，获取结果失败。');
    }

    return this.sanitizeMarkdown(finalText);
  }

  /**
   * 清洗 Teams Copilot 自动添加的废话文本：
   *  - 前缀寒暄（"好的，这是您的PRD..."）
   *  - 后缀追问（"是否需要我继续?"）
   *  - 多余的反引号包裹
   *  - 续写提示词残留
   *  - 用户 prompt 残留
   */
  private sanitizeMarkdown(raw: string): string {
    let cleaned = raw;

    // 移除续写提示词残留
    cleaned = cleaned.replace(/请严格从你断开的地方继续输出，不要重复已输出的内容，不要任何前缀。/g, '');
    cleaned = cleaned.replace(/请继续/g, '');

    // 移除用户 prompt 残留（常见模式）
    cleaned = cleaned.replace(/请直接输出完整回答，不要有任何前缀寒暄和后缀追问。/g, '');
    cleaned = cleaned.replace(/请直接输出 Markdown 格式的 PRD，不要有任何前缀寒暄和后缀追问。/g, '');
    cleaned = cleaned.replace(/请直接输出 Markdown 格式的架构设计文档，不要有任何前缀寒暄和后缀追问。/g, '');
    cleaned = cleaned.replace(/请直接输出 Markdown 格式，不要有任何前缀寒暄和后缀追问。/g, '');

    // 移除常见的 AI 寒暄前缀行
    cleaned = cleaned.replace(
      /^(好的[，,]|收到[，,]|明白了[，,]|以下是|Here is|Here are|让我|让我来|我来|Sure[!,.]|Of course[!,.]).*\n?/i,
      '',
    );

    // 移除常见的后缀追问
    cleaned = cleaned.replace(
      /\n*(是否需要我.*|需要我.*吗[？?]|还有其他.*吗[？?]|需要进一步.*吗[？?]|Let me know if.*|Do you want.*)\n*$/i,
      '',
    );

    // 如果整个内容被 ```markdown ... ``` 包裹，去掉外层
    const codeBlockMatch = cleaned.match(/^```(?:markdown|md)?\s*\n([\s\S]*?)\n```$/i);
    if (codeBlockMatch) {
      cleaned = codeBlockMatch[1];
    }

    return cleaned.trim();
  }

  /**
   * 获取错误码（供 CLI 退出码使用）。
   */
  static getErrorCode(err: unknown): number {
    if (err instanceof Error) {
      return mapErrorCode(err.message);
    }
    return 1;
  }

  /** 获取当前 adapter（供高级操作）。 */
  getAdapter(): OpenCLIAdapter {
    return this.adapter;
  }
}
