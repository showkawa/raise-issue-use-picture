#!/usr/bin/env node
import { Command } from 'commander';
import { askCommand } from './ask.js';
import { prdCommand } from './prd.js';
import { archCommand } from './arch.js';
import { tasksCommand } from './tasks.js';

const program = new Command();

program
  .name('teams-copilot')
  .description('Teams Copilot CLI — AI Coding assistant via browser automation')
  .version('2.0.0')
  .option('--config <path>', 'Path to config.yaml')
  .option('--browser <path>', 'Browser executable path')
  .option('--port <number>', 'CDP debugging port')
  .option('--no-stream', 'Disable streaming output');

program
  .command('ask <question...>')
  .description('Ask Copilot a question')
  .action(async (question: string[], opts) => {
    const globalOpts = program.opts();
    await askCommand(question.join(' '), { ...globalOpts, ...opts });
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
  await program.parseAsync();
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
