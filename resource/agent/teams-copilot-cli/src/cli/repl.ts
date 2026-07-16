import * as readline from 'readline';
import { createRuntime } from '../provider/copilot-web/copilot-runtime.js';
import type { CopilotSession } from '../types.js';
import type { CommandOpts } from './ask.js';
import { browserFlagsFromOptions } from './utils.js';

export async function replLoop(opts: CommandOpts): Promise<void> {
  const runtime = await createRuntime(opts.config, browserFlagsFromOptions(opts));
  let session: CopilotSession | null = null;
  try {
    session = await runtime.createSession();
    await runRepl(session, opts.stream !== false);
  } finally {
    await session?.close();
    await runtime.close();
  }
}

async function runRepl(session: CopilotSession, initialStream: boolean): Promise<void> {
  let stream = initialStream;
  let busy = false;
  let activeRequest: Promise<void> | null = null;
  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
    prompt: 'copilot> ',
  });

  console.log('Microsoft 365 Copilot CLI v2 — Type /help for commands, /exit to quit\n');

  rl.on('SIGINT', () => rl.close());
  rl.on('line', (line) => {
    if (busy) {
      process.stderr.write('A request is already running. Wait for it to finish.\n');
      rl.prompt();
      return;
    }
    activeRequest = handleLine(line).finally(() => {
      activeRequest = null;
    });
  });

  async function handleLine(line: string): Promise<void> {
    const input = line.trim();
    if (input === '/exit' || input === '/quit' || input === 'exit' || input === 'quit') {
      rl.close();
      return;
    }
    if (input === '/help') {
      process.stdout.write('/help, /exit, /quit, /clear, /stream on, /stream off\n');
      rl.prompt();
      return;
    }
    if (input === '/clear') {
      console.clear();
      rl.prompt();
      return;
    }
    if (input === '/stream on' || input === '/stream off') {
      stream = input.endsWith('on');
      process.stdout.write(`Streaming ${stream ? 'enabled' : 'disabled'}.\n`);
      rl.prompt();
      return;
    }
    if (!input) {
      rl.prompt();
      return;
    }

    busy = true;
    rl.pause();
    try {
      if (!stream) process.stdout.write('Thinking...\n');
      const result = await session.ask(input, {
        onUpdate: stream ? (chunk) => process.stdout.write(chunk) : undefined,
      });
      process.stdout.write(stream ? '\n\n' : `${result.text}\n\n`);
      if (result.truncated) {
        process.stderr.write('[Warning: Response was truncated]\n');
      }
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : String(error);
      process.stderr.write(`Error: ${message}\n\n`);
    } finally {
      busy = false;
      rl.resume();
      rl.prompt();
    }
  }

  rl.prompt();
  await new Promise<void>((resolve) => rl.once('close', resolve));
  await activeRequest;
}
