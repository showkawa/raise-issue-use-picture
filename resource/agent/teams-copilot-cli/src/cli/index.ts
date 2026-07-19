#!/usr/bin/env node
import { Command } from 'commander';
import { readDelimitedQuestion } from './prompt-content.js';
import { runFiveWhys } from './five-whys-command.js';

const program = new Command();

program
  .name('tcc')
  .description('Guided 5 Whys root-cause analysis via teams-copilot-proxy')
  .version('2.0.3')
  .argument('<problem...>', 'The problem to analyze; use "tcc @" to enter multiline input')
  .option('--config <path>', 'Path to config.yaml')
  .option('-o, --output <path>', 'Save the final summary to a Markdown file')
  .option('--no-stream', 'Print each answer at once instead of streaming')
  .action(async (problemParts: string[], opts: { config?: string; output?: string; stream?: boolean }) => {
    const problem = problemParts.length === 1 && problemParts[0] === '@'
      ? await readDelimitedQuestion()
      : problemParts.join(' ');
    await runFiveWhys(problem, opts);
  });

try {
  await program.parseAsync(process.argv);
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
