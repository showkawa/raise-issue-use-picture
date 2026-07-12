import { createRuntime } from '../runtime/copilot-runtime.js';
import { browserFlagsFromOptions } from './utils.js';

export interface CommandOpts {
  config?: string;
  browser?: string;
  port?: string;
  stream?: boolean;
}

export async function askCommand(question: string, opts: CommandOpts): Promise<void> {
  const runtime = await createRuntime(opts.config, browserFlagsFromOptions(opts));
  const stream = opts.stream !== false;
  try {
    const result = await runtime.ask(question, {
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
