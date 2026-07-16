import { writeFileSync } from 'fs';
import { join } from 'path';
import { createRuntime } from '../provider/copilot-web/copilot-runtime.js';
import type { CommandOpts } from './ask.js';
import { browserFlagsFromOptions, ensureOutputDir, readPromptTemplate, readRequiredFile } from './utils.js';

export async function tasksCommand(projectName: string, opts: CommandOpts): Promise<void> {
  const prdPath = join(process.cwd(), 'output', 'PRD.md');
  const archPath = join(process.cwd(), 'output', 'ARCH.md');
  const prdContent = readRequiredFile(
    prdPath,
    `PRD file not found at output/PRD.md. Run "teams-copilot prd ${projectName}" first.`,
  );
  const archContent = readRequiredFile(
    archPath,
    `Architecture file not found at output/ARCH.md. Run "teams-copilot arch ${projectName}" first.`,
  );
  let prompt = readPromptTemplate('tasks.md');
  prompt = prompt.replace(/\{project_name\}/g, projectName);
  prompt = prompt.replace(/\{prd_content\}/g, prdContent);
  prompt = prompt.replace(/\{arch_content\}/g, archContent);

  const runtime = await createRuntime(opts.config, browserFlagsFromOptions(opts));
  const stream = opts.stream !== false;
  try {
    process.stdout.write(`Generating tasks for "${projectName}"...\n`);
    const result = await runtime.ask(prompt, {
      onUpdate: stream ? (chunk) => process.stdout.write(chunk) : undefined,
    });
    if (stream) process.stdout.write('\n');

    const outputDir = ensureOutputDir();
    writeFileSync(join(outputDir, 'TASKS.md'), result.text);
    process.stdout.write(`Tasks saved to output/TASKS.md\n`);
  } finally {
    await runtime.close();
  }
}
