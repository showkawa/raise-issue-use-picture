#!/usr/bin/env node
import { Command } from 'commander';
import { askCommand } from './ask.js';
import { prdCommand } from './prd.js';
import { archCommand } from './arch.js';
import { tasksCommand } from './tasks.js';
import { normalizeCliArgv } from './argv.js';
import { reviewCommand } from './review.js';

const program = new Command();

program
  .name('tcc')
  .description('Microsoft 365 Copilot CLI — AI Coding assistant via browser automation')
  .version('2.0.3')
  .option('--config <path>', 'Path to config.yaml')
  .option('--browser <path>', 'Browser executable path')
  .option('--port <number>', 'CDP debugging port')
  .option('--no-stream', 'Disable streaming output');

program
  .command('ask <question...>')
  .description('Ask Copilot a question')
  .option('-f, --file <path>', 'Append a local text file as a Markdown code block')
  .option('--stdin', 'Read piped text from stdin and append it as a Markdown code block')
  .option('-l, --language <name>', 'Override the Markdown code fence language')
  .option('-o, --output <path>', 'Save the response to a local file')
  .action(async (question: string[], opts) => {
    const globalOpts = program.opts();
    await askCommand(question.join(' '), { ...globalOpts, ...opts });
  });

program
  .command('review <file>')
  .description('Upload a local code file and ask Copilot to review it')
  .option('-o, --output <path>', 'Save the Markdown review to a local file')
  .action(async (file: string, opts) => {
    const globalOpts = program.opts();
    await reviewCommand(file, { ...globalOpts, ...opts });
  });

program
  .command('prd <project-name>')
  .description('Generate a PRD document')
  .action(async (name: string, opts) => {
    const globalOpts = program.opts();
    await prdCommand(name, { ...globalOpts, ...opts });
  });

program
  .command('arch <project-name>')
  .description('Generate architecture design')
  .action(async (name: string, opts) => {
    const globalOpts = program.opts();
    await archCommand(name, { ...globalOpts, ...opts });
  });

program
  .command('tasks <project-name>')
  .description('Generate task breakdown')
  .action(async (name: string, opts) => {
    const globalOpts = program.opts();
    await tasksCommand(name, { ...globalOpts, ...opts });
  });

program
  .command('repl')
  .description('Interactive REPL chat with Copilot')
  .action(async () => {
    const { replLoop } = await import('./repl.js');
    await replLoop(program.opts());
  });

try {
  await program.parseAsync(normalizeCliArgv(process.argv));
} catch (error) {
  const message = error instanceof Error ? error.message : String(error);
  process.stderr.write(`Error: ${message}\n`);
  process.exitCode = (
    typeof error === 'object'
    && error !== null
    && 'exitCode' in error
    && typeof error.exitCode === 'number'
  ) ? error.exitCode : 1;
}
