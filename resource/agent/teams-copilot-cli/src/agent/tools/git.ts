import { spawn } from 'child_process';
import type { Tool, ToolContext, ToolResult } from './types.js';

const SUBCOMMANDS = ['status', 'diff', 'log', 'add', 'commit'] as const;
type GitSubcommand = typeof SUBCOMMANDS[number];

const MAX_OUTPUT_CHARS = 30000;

export interface GitArgs {
  subcommand: GitSubcommand;
  args?: string[];
  message?: string;
}

function runGit(cliArgs: string[], cwd: string): Promise<ToolResult> {
  return new Promise((resolvePromise) => {
    const child = spawn('git', cliArgs, { cwd, windowsHide: true });
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', (chunk: Buffer) => { stdout += chunk.toString('utf8'); });
    child.stderr.on('data', (chunk: Buffer) => { stderr += chunk.toString('utf8'); });
    child.on('error', (error) => {
      resolvePromise({ ok: false, output: `Failed to run git: ${error.message}` });
    });
    child.on('close', (code) => {
      const exitCode = code ?? -1;
      const output = [stdout, stderr && `stderr:\n${stderr}`].filter(Boolean).join('\n');
      resolvePromise({
        ok: exitCode === 0,
        exitCode,
        output: output.slice(0, MAX_OUTPUT_CHARS) || '(no output)',
      });
    });
  });
}

const FORBIDDEN_FLAGS = new Set(['--force', '-f', '--hard', '--no-verify', '--amend', '-A', '--all', '.']);

export const gitTool: Tool<GitArgs> = {
  name: 'git',
  description: 'git 白名单子命令：status | diff | log | add | commit（commit 需 message；add 需逐文件传 args）',
  risk: 'exec',
  schema: {
    type: 'object',
    properties: {
      subcommand: { type: 'string', enum: [...SUBCOMMANDS] },
      args: { type: 'array', items: { type: 'string' }, description: '附加参数（文件路径等）' },
      message: { type: 'string', description: 'commit 提交信息' },
    },
    required: ['subcommand'],
  },
  async run(args: GitArgs, ctx: ToolContext): Promise<ToolResult> {
    if (!SUBCOMMANDS.includes(args.subcommand)) {
      return { ok: false, output: `Unsupported git subcommand: ${String(args.subcommand)}` };
    }
    const extra = args.args ?? [];
    const forbidden = extra.find((arg) => FORBIDDEN_FLAGS.has(arg));
    if (forbidden) {
      return { ok: false, output: `Argument not allowed: ${forbidden}` };
    }
    if (args.subcommand === 'commit') {
      if (!args.message) return { ok: false, output: 'commit requires a message' };
      return runGit(['commit', '-m', args.message, ...extra], ctx.projectRoot);
    }
    if (args.subcommand === 'add' && extra.length === 0) {
      return { ok: false, output: 'add requires explicit file paths in args' };
    }
    if (args.subcommand === 'log' && extra.length === 0) {
      return runGit(['log', '--oneline', '-20'], ctx.projectRoot);
    }
    return runGit([args.subcommand, ...extra], ctx.projectRoot);
  },
};
