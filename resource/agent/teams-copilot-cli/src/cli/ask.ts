import { createRuntime } from '../runtime/copilot-runtime.js';
import { browserFlagsFromOptions } from './utils.js';
import {
  buildCodePrompt,
  inferCodeLanguage,
  readStandardInput,
  readTextFile,
} from './prompt-content.js';

const INLINE_PROMPT_WARNING_LENGTH = 8000;

export interface CommandOpts {
  config?: string;
  browser?: string;
  port?: string;
  stream?: boolean;
}

export interface AskCommandOpts extends CommandOpts {
  file?: string;
  stdin?: boolean;
  language?: string;
}

export async function askCommand(question: string, opts: AskCommandOpts): Promise<void> {
  if (opts.file && opts.stdin) {
    throw new Error('Use either --file or --stdin, not both');
  }
  let prompt = question;
  if (opts.file) {
    prompt = buildCodePrompt(
      question,
      readTextFile(opts.file),
      opts.language ?? inferCodeLanguage(opts.file),
    );
  } else if (opts.stdin) {
    prompt = buildCodePrompt(
      question,
      await readStandardInput(),
      opts.language ?? 'text',
    );
  }
  if (prompt.length > INLINE_PROMPT_WARNING_LENGTH) {
    process.stderr.write(
      `[Warning: inline prompt is ${prompt.length} characters; `
      + 'the current Copilot tenant may reject or truncate it]\n',
    );
  }

  const runtime = await createRuntime(opts.config, browserFlagsFromOptions(opts));
  const stream = opts.stream !== false;
  try {
    const result = await runtime.ask(prompt, {
      onUpdate: stream ? (chunk) => process.stdout.write(chunk) : undefined,
    });
    if (stream) {
      process.stdout.write('\n');
    } else {
      process.stdout.write(result.text + '\n');
    }
    if (result.truncated) {
      process.stderr.write('[Warning: Response was truncated]\n');
    }
  } finally {
    await runtime.close();
  }
}
