import { writeFileSync } from 'fs';
import { join } from 'path';
import { createRuntime } from '../runtime/copilot-runtime.js';
import type { CommandOpts } from './ask.js';
import { browserFlagsFromOptions, ensureOutputDir, readPromptTemplate, readRequiredFile } from './utils.js';

export async function archCommand(projectName: string, opts: CommandOpts): Promise<void> {
  const prdPath = join(process.cwd(), 'output', 'PRD.md');
  const prdContent = readRequiredFile(
    prdPath,
    `PRD file not found at output/PRD.md. Run "teams-copilot prd ${projectName}" first.`,
  );
  let prompt = readPromptTemplate('arch.md');
  prompt = prompt.replace(/\{project_name\}/g, projectName);
  prompt = prompt.replace(/\{prd_content\}/g, prdContent);

  const runtime = await createRuntime(opts.config, browserFlagsFromOptions(opts));
  const stream = opts.stream !== false;
  try {
    process.stdout.write(`Generating architecture for "${projectName}"...\n`);
    const result = await runtime.ask(prompt, {
      onUpdate: stream ? (chunk) => process.stdout.write(chunk) : undefined,
    });
    if (stream) process.stdout.write('\n');

    const outputDir = ensureOutputDir();
    writeFileSync(join(outputDir, 'ARCH.md'), result.text);
    process.stdout.write(`Architecture saved to output/ARCH.md\n`);
  } finally {
    await runtime.close();
  }
}
