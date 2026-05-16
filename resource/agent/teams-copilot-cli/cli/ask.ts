/**
 * cli/ask.ts — 自由问答命令
 * 用法: bun run cli/ask.ts "<your-question>"
 */

import { CopilotRuntime } from '../runtime/copilot-runtime.js';

const runtime = new CopilotRuntime();

export async function askCommand(question: string, configPath?: string): Promise<void> {
  if (!question || !question.trim()) {
    console.error('用法: bun run cli/ask.ts "<your-question>"');
    console.error('示例: bun run cli/ask.ts "如何设计一个高可用的消息队列？"');
    process.exit(1);
  }

  console.log('[ASK] Sending question to Teams Copilot...');

  const prompt = question.trim() + '\n\n请直接输出完整回答，不要有任何前缀寒暄和后缀追问。';

  try {
    await runtime.init(configPath);
    await runtime.ensureSession();
    await runtime.triggerCopilotAndInput(prompt);

    const markdownContent = await runtime.fetchResult();

    console.log('\n--- Copilot 回复 ---\n');
    console.log(markdownContent);
    console.log('\n--- 回复结束 ---\n');
  } catch (error) {
    console.error(`[ASK] Error: ${error instanceof Error ? error.message : String(error)}`);
    process.exit(CopilotRuntime.getErrorCode(error));
  }
}

// CLI 直接调用（仅当文件作为入口运行时执行）
if (import.meta.main) {
  const question = process.argv[2];
  askCommand(question);
}
