import { resolve } from 'path';
import { createRuntime } from '../runtime/copilot-runtime.js';
import type { CommandOpts } from './ask.js';
import { browserFlagsFromOptions } from './utils.js';
import { writeTextOutput } from './prompt-content.js';

const REVIEW_PROMPT = [
  '请审查附件中的代码文件。',
  '按严重程度列出正确性、安全性、性能、可维护性和测试覆盖方面的问题。',
  '每个问题应包含文件名、尽可能准确的行号、原因和可执行的修改建议。',
  '如果没有发现问题，请明确说明。只输出 Markdown review 报告。',
].join('\n');

export interface ReviewCommandOpts extends CommandOpts {
  output?: string;
}

export async function reviewCommand(
  filePath: string,
  opts: ReviewCommandOpts,
): Promise<void> {
  if (opts.output && resolve(opts.output) === resolve(filePath)) {
    throw new Error('Review output path must not overwrite the source code file');
  }
  const runtime = await createRuntime(opts.config, browserFlagsFromOptions(opts));
  const stream = opts.stream !== false;
  try {
    const result = await runtime.askWithFile(filePath, REVIEW_PROMPT, {
      onUpdate: stream ? (chunk) => process.stdout.write(chunk) : undefined,
      autoContinue: false,
    });
    if (stream) {
      process.stdout.write('\n');
    } else {
      process.stdout.write(`${result.text}\n`);
    }
    if (opts.output) {
      const outputPath = writeTextOutput(opts.output, result.text);
      process.stderr.write(`Review saved to ${outputPath}\n`);
    }
    if (result.truncated) {
      process.stderr.write('[Warning: Review response was truncated]\n');
    }
  } finally {
    await runtime.close();
  }
}
