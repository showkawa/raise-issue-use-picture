#!/usr/bin/env node
import { Command } from 'commander';
import { askCommand } from './ask.js';
import { prdCommand } from './prd.js';
import { archCommand } from './arch.js';
import { tasksCommand } from './tasks.js';
import { normalizeCliArgv } from './argv.js';
import { readDelimitedQuestion } from './prompt-content.js';
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
  .description('Ask Copilot a question; use "tcc @" for multiline input')
  .option('-f, --file <path>', 'Append a local text file as a Markdown code block')
  .option('--stdin', 'Read piped text from stdin and append it as a Markdown code block')
  .option('-l, --language <name>', 'Override the Markdown code fence language')
  .option('-o, --output <path>', 'Save the response to a local file')
  .action(async (question: string[], opts) => {
    const globalOpts = program.opts();
    const prompt = question.length === 1 && question[0] === '@'
      ? await readDelimitedQuestion()
      : question.join(' ');
    await askCommand(prompt, { ...globalOpts, ...opts });
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
  .command('code <task...>')
  .description('Run the coding agent on a task in the current repo; use @file to attach file contents')
  .option('--permission-mode <mode>', 'yolo | allowlist | ask (default: allowlist)')
  .option('--yolo', 'Shortcut for --permission-mode yolo (auto-runs every tool; dangerous)')
  .option('--ask', 'Shortcut for --permission-mode ask (confirm every tool call)')
  .option('--max-iterations <n>', 'Max agent iterations')
  .action(async (task: string[], opts) => {
    const globalOpts = program.opts();
    const { codeCommand } = await import('./code.js');
    const prompt = task.length === 1 && task[0] === '@'
      ? await readDelimitedQuestion()
      : task.join(' ');
    await codeCommand(prompt, { ...globalOpts, ...opts });
  });

program
  .command('implement')
  .description('Execute pending checkbox tasks from a TASKS.md via the coding agent')
  .option('--tasks <path>', 'Tasks file path (default: output/TASKS.md)')
  .option('--task <id>', 'Run only the task with this id (e.g. T2)')
  .option('--continue-on-failure', 'Continue with the next task when one fails')
  .option('--commit', 'Auto git-commit changed files after each successful task (requires a clean worktree)')
  .option('--permission-mode <mode>', 'yolo | allowlist | ask (default: allowlist)')
  .option('--yolo', 'Shortcut for --permission-mode yolo (auto-runs every tool; dangerous)')
  .option('--ask', 'Shortcut for --permission-mode ask (confirm every tool call)')
  .option('--max-iterations <n>', 'Max agent iterations per task')
  .action(async (opts) => {
    const globalOpts = program.opts();
    const { implementCommand } = await import('./implement.js');
    await implementCommand({ ...globalOpts, ...opts });
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
