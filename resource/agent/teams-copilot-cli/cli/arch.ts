/**
 * cli/arch.ts — 生成架构设计文档命令
 * 用法: bun run cli/arch.ts <project-name> [--prd <prd-file-path>]
 */

import { CopilotRuntime } from '../runtime/copilot-runtime.js';
import fs from 'node:fs';
import path from 'node:path';

const runtime = new CopilotRuntime();

export async function archCommand(
  projectName: string,
  prdPath?: string,
  configPath?: string,
): Promise<void> {
  console.log(`[ARCH] Generating architecture design for: ${projectName}...`);

  // 1. 读取 PRD 内容（优先使用指定路径，否则尝试默认路径）
  const defaultPrdPath = path.resolve(
    import.meta.dirname ?? process.cwd(),
    '..',
    'output',
    'PRD.md',
  );
  const resolvedPrdPath = prdPath ?? defaultPrdPath;

  let prdContent: string;
  try {
    prdContent = fs.readFileSync(resolvedPrdPath, 'utf-8');
  } catch {
    throw new Error(
      `PRD 文件不存在: ${resolvedPrdPath}\n请先运行 "bun run cli/prd.ts ${projectName}" 生成 PRD。`,
    );
  }

  // 2. 读取并组装 Prompt
  let promptTemplate = fs.readFileSync(
    path.resolve(import.meta.dirname ?? process.cwd(), '..', 'prompts', 'arch.md'),
    'utf-8',
  );
  const finalPrompt = promptTemplate
    .replace(/\{project_name\}/g, projectName)
    .replace(/\{prd_content\}/g, prdContent) +
    '\n\n请直接输出 Markdown 格式的架构设计文档，不要有任何前缀寒暄和后缀追问。';

  try {
    await runtime.init(configPath);
    await runtime.ensureSession();
    await runtime.triggerCopilotAndInput(finalPrompt);

    const markdownContent = await runtime.fetchResult();

    const outputDir = path.resolve(
      import.meta.dirname ?? process.cwd(),
      '..',
      'output',
    );
    fs.mkdirSync(outputDir, { recursive: true });
    const outputPath = path.join(outputDir, 'ARCH.md');
    fs.writeFileSync(outputPath, markdownContent, 'utf-8');
    console.log(`[ARCH] Successfully generated at: ${outputPath}`);
  } catch (error) {
    console.error(`[ARCH] Error: ${error instanceof Error ? error.message : String(error)}`);
    process.exit(CopilotRuntime.getErrorCode(error));
  }
}

// CLI 直接调用
const args = process.argv.slice(2);
const projectName = args[0];
const prdIndex = args.indexOf('--prd');
const prdPath = prdIndex !== -1 ? args[prdIndex + 1] : undefined;

if (!projectName) {
  console.error('用法: bun run cli/arch.ts <project-name> [--prd <prd-file-path>]');
  console.error('示例: bun run cli/arch.ts my-awesome-app');
  process.exit(1);
}

archCommand(projectName, prdPath);
