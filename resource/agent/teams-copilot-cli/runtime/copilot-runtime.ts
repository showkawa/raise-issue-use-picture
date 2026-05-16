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
import { OBSERVER_SCRIPT } from './observer-injector.js';
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
   *  3. 注入 MutationObserver 监听脚本
   */
  async ensureSession(): Promise<void> {
    const page = this.adapter.getPage();
    if (!page) throw new Error('[CopilotRuntime] Page not initialized');

    await page.goto(this.config.copilot.url, { waitUntil: 'load', settleMs: 3000 });

    // opencli IPage 没有 url() 方法，通过 evaluate 获取
    const url = (await page.evaluate(() => window.location.href)) as string;
    if (!isAuthenticated(url)) {
      throw new Error(
        'AUTH_EXPIRED: 请在弹出的浏览器中手动完成 MFA 登录，然后重新运行命令。',
      );
    }

    // 注入流式监听脚本
    await page.evaluate(OBSERVER_SCRIPT);
  }

  /**
   * 导航到 Copilot 并注入 Prompt。
   * 优先使用 CSS 属性选择器（ARIA），data-tid fallback 兜底。
   */
  async triggerCopilotAndInput(prompt: string): Promise<void> {
    // 点击 Copilot 入口
    try {
      await this.adapter.click('button[aria-label*="Copilot"]');
    } catch {
      try {
        await this.adapter.click('[data-tid="app-bar-copilot-icon"]');
      } catch {
        console.log('[CopilotRuntime] Copilot entry not found, assuming already open.');
      }
    }

    // 等待输入框可用（CSS 选择器，非 Playwright role 语法）
    await this.adapter.waitForSelector(
      this.config.copilot.inputSelector,
      15000,
    );

    // 注入 Prompt
    await this.adapter.injectPrompt(prompt, this.config.copilot.inputSelector);

    // 发送
    await this.adapter.send();
  }

  /**
   * 流式获取结果，包含截断检测与自动续写。
   *
   * 流程：
   *  1. 等待流式输出结束（observer 标志位）
   *  2. 防抖确认文本稳定（3 个循环）
   *  3. 检测截断 → 自动发送续写 Prompt
   *  4. 清洗 Teams 添加的废话（前缀寒暄/后缀追问）
   */
  async fetchResult(): Promise<string> {
    let finalText = '';
    let lastLength = 0;
    let stableCount = 0;

    const timeout = this.config.copilot.timeout;

    while (true) {
      try {
        await this.adapter.waitForStreamingEnd(timeout);

        const currentText = await this.adapter.readBuffer();

        // 防抖：判断文本是否真正停止变化
        if (currentText.length === lastLength) {
          stableCount++;
        } else {
          stableCount = 0;
        }

        if (stableCount > 3 && currentText.length > 0) {
          finalText = currentText;

          // 检测截断：末尾无标点，或代码块未闭合
          const isTruncated =
            !/[.?!。？！\n`]{1,2}$/.test(finalText.trim()) ||
            (finalText.split('```').length % 2 === 0);

          if (isTruncated) {
            console.log('[CopilotRuntime] Detected truncation, sending continue prompt...');
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
        throw new Error(
          `STREAMING_TIMEOUT: 生成超时（${timeout}ms），请检查网络或 Copilot 状态。` +
            (err instanceof Error ? `\n原始错误: ${err.message}` : ''),
        );
      }
    }

    return this.sanitizeMarkdown(finalText);
  }

  /**
   * 清洗 Teams Copilot 自动添加的废话文本：
   *  - 前缀寒暄（"好的，这是您的PRD..."）
   *  - 后缀追问（"是否需要我继续?"）
   *  - 多余的反引号包裹
   */
  private sanitizeMarkdown(raw: string): string {
    let cleaned = raw;

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
