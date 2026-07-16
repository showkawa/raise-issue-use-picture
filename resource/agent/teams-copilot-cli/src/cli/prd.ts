import { writeFileSync } from 'fs';
import { join } from 'path';
import { createRuntime } from '../provider/copilot-web/copilot-runtime.js';
import type { CommandOpts } from './ask.js';
import { browserFlagsFromOptions, ensureOutputDir, readPromptTemplate } from './utils.js';

export async function prdCommand(projectName: string, opts: CommandOpts): Promise<void> {
  let prompt = readPromptTemplate('prd.md');
  prompt = prompt.replace(/\{project_name\}/g, projectName);

  const runtime = await createRuntime(opts.config, browserFlagsFromOptions(opts));
  const stream = opts.stream !== false;
  try {
    process.stdout.write(`Generating PRD for "${projectName}"...\n`);
    const result = await runtime.ask(prompt, {
      onUpdate: stream ? (chunk) => process.stdout.write(chunk) : undefined,
    });
    if (stream) process.stdout.write('\n');

    const outputDir = ensureOutputDir();
    writeFileSync(join(outputDir, 'PRD.md'), result.text);
    process.stdout.write(`PRD saved to output/PRD.md\n`);
  } finally {
    await runtime.close();
  }
}
