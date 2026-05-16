/**
 * cli/tasks.ts — 生成任务拆解文档命令
 * 用法: bun run cli/tasks.ts <project-name> [--prd <prd-path>] [--arch <arch-path>]
 */

import { CopilotRuntime } from '../runtime/copilot-runtime.js';
import fs from 'node:fs';
import path from 'node:path';

const runtime = new CopilotRuntime();

export async function tasksCommand(
  projectName: string,
  prdPath?: string,
  archPath?: string,
  configPath?: string,
): Promise<void> {
  console.log(`[TASKS] Generating task breakdown for: ${projectName}...`);

  const outputDir = path.resolve(import.meta.dirname ?? process.cwd(), '..', 'output');

  // 1. 读取 PRD
  const resolvedPrdPath = prdPath ?? path.join(outputDir, 'PRD.md');
  let prdContent: string;
  try {
    prdContent = fs.readFileSync(resolvedPrdPath, 'utf-8');
  } catch {
    throw new Error(
      `PRD 文件不存在: ${resolvedPrdPath}\n请先运行 "bun run cli/prd.ts ${projectName}" 生成 PRD。`,
    );
  }

  // 2. 读取架构设计
  const resolvedArchPath = archPath ?? path.join(outputDir, 'ARCH.md');
  let archContent: string;
  try {
    archContent = fs.readFileSync(resolvedArchPath, 'utf-8');
  } catch {
    throw new Error(
      `架构文档不存在: ${resolvedArchPath}\n请先运行 "bun run cli/arch.ts ${projectName}" 生成架构设计。`,
    );
  }

  // 3. 读取并组装 Prompt
  let promptTemplate = fs.readFileSync(
    path.resolve(import.meta.dirname ?? process.cwd(), '..', 'prompts', 'tasks.md'),
    'utf-8',
  );
  const finalPrompt = promptTemplate
    .replace(/\{project_name\}/g, projectName)
    .replace(/\{prd_content\}/g, prdContent)
    .replace(/\{arch_content\}/g, archContent) +
    '\n\n请直接输出 Markdown 格式，不要有任何前缀寒暄和后缀追问。';

  try {
    await runtime.init(configPath);
    await runtime.ensureSession();
    await runtime.triggerCopilotAndInput(finalPrompt);

    const markdownContent = await runtime.fetchResult();

    fs.mkdirSync(outputDir, { recursive: true });
    const outputPath = path.join(outputDir, 'TASKS.md');
    fs.writeFileSync(outputPath, markdownContent, 'utf-8');
    console.log(`[TASKS] Successfully generated at: ${outputPath}`);
  } catch (error) {
    console.error(`[TASKS] Error: ${error instanceof Error ? error.message : String(error)}`);
    process.exit(CopilotRuntime.getErrorCode(error));
  }
}

// CLI 直接调用（仅当文件作为入口运行时执行）
if (import.meta.main) {
const args = process.argv.slice(2);
const projectName = args[0];
const prdIndex = args.indexOf('--prd');
const archIndex = args.indexOf('--arch');
const prdPath = prdIndex !== -1 ? args[prdIndex + 1] : undefined;
const archPath = archIndex !== -1 ? args[archIndex + 1] : undefined;

if (!projectName) {
  console.error('用法: bun run cli/tasks.ts <project-name> [--prd <prd-path>] [--arch <arch-path>]');
  console.error('示例: bun run cli/tasks.ts my-awesome-app');
  process.exit(1);
}

  tasksCommand(projectName, prdPath, archPath);
}
