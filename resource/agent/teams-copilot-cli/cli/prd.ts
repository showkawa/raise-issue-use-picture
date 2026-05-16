/**
 * cli/prd.ts — 生成 PRD 文档命令
 * 用法: bun run cli/prd.ts <project-name>
 */

import { CopilotRuntime } from '../runtime/copilot-runtime.js';
import fs from 'node:fs';
import path from 'node:path';

const runtime = new CopilotRuntime();

export async function prdCommand(projectName: string, configPath?: string): Promise<void> {
  console.log(`[PRD] Generating PRD for: ${projectName}...`);

  // 1. 读取并组装 Prompt
  const promptTemplate = fs.readFileSync(
    path.resolve(import.meta.dirname ?? process.cwd(), '..', 'prompts', 'prd.md'),
    'utf-8',
  );
  const finalPrompt =
    promptTemplate.replace(/\{project_name\}/g, projectName) +
    '\n\n请直接输出 Markdown 格式的 PRD，不要有任何前缀寒暄和后缀追问。';

  try {
    // 2. 启动运行时
    await runtime.init(configPath);
    await runtime.ensureSession();

    // 3. 交互
    await runtime.triggerCopilotAndInput(finalPrompt);

    // 4. 提取结果
    const markdownContent = await runtime.fetchResult();

    // 5. 持久化
    const outputDir = path.resolve(process.cwd(), 'output');
    if (!fs.existsSync(outputDir)) {
      fs.mkdirSync(outputDir, { recursive: true });
    }
    const outputPath = path.join(outputDir, 'PRD.md');
    fs.writeFileSync(outputPath, markdownContent, 'utf-8');
    console.log(`[PRD] Successfully generated at: ${outputPath}`);

    // 同时保存一份在项目 output 目录
    const localOutputPath = path.resolve(
      import.meta.dirname ?? process.cwd(),
      '..',
      'output',
      'PRD.md',
    );
    fs.mkdirSync(path.dirname(localOutputPath), { recursive: true });
    fs.writeFileSync(localOutputPath, markdownContent, 'utf-8');
  } catch (error) {
    console.error(`[PRD] Error: ${error instanceof Error ? error.message : String(error)}`);
    process.exit(CopilotRuntime.getErrorCode(error));
  }
}

// CLI 直接调用（仅当文件作为入口运行时执行）
if (import.meta.main) {
  const projectName = process.argv[2];
  if (!projectName) {
    console.error('用法: bun run cli/prd.ts <project-name>');
    console.error('示例: bun run cli/prd.ts my-awesome-app');
    process.exit(1);
  }
  prdCommand(projectName);
}
