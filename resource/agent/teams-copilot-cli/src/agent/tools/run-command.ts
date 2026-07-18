import { spawn } from 'child_process';
import type { Tool, ToolContext, ToolResult } from './types.js';
import { resolveInsideRoot } from './fs-utils.js';
import { detectInteractive } from '../command-classify.js';

const DEFAULT_TIMEOUT_MS = 120000;
// ~64KB total output cap: ~32KB per stream.
const MAX_OUTPUT_CHARS = 32 * 1024;

export interface RunCommandArgs {
  command: string;
  cwd?: string;
  timeoutMs?: number;
}

function shellFor(command: string): { file: string; args: string[] } {
  if (process.platform === 'win32') {
    return {
      file: 'powershell.exe',
      args: [
        '-NoProfile',
        '-NonInteractive',
        '-Command',
        `[Console]::OutputEncoding=[Text.Encoding]::UTF8; ${command}; exit $LASTEXITCODE`,
      ],
    };
  }
  return { file: '/bin/sh', args: ['-c', command] };
}

export const runCommandTool: Tool<RunCommandArgs> = {
  name: 'run_command',
  description: '在项目根（或 cwd）下执行 shell 命令（Windows 上为 PowerShell），返回 stdout/stderr/exitCode；仅支持有限时命令，不要启动 dev server 等常驻进程',
  risk: 'exec',
  schema: {
    type: 'object',
    properties: {
      command: { type: 'string' },
      cwd: { type: 'string', description: '相对项目根的工作目录' },
      timeoutMs: { type: 'number' },
    },
    required: ['command'],
  },
  async run(args: RunCommandArgs, ctx: ToolContext): Promise<ToolResult> {
    const interactive = detectInteractive(args.command);
    if (interactive.interactive) {
      return {
        ok: false,
        output: `拒绝执行交互式命令：${interactive.reason}。请改用非交互参数（如 --yes / --no-input / -Command）。`,
      };
    }
    let cwd: string;
    try {
      cwd = args.cwd ? resolveInsideRoot(ctx.projectRoot, args.cwd) : ctx.projectRoot;
    } catch (error) {
      return { ok: false, output: error instanceof Error ? error.message : String(error) };
    }
    const timeoutMs = Math.min(Math.max(1000, args.timeoutMs ?? DEFAULT_TIMEOUT_MS), 600000);
    const { file, args: shellArgs } = shellFor(args.command);

    return new Promise<ToolResult>((resolvePromise) => {
      const child = spawn(file, shellArgs, { cwd, windowsHide: true });
      let stdout = '';
      let stderr = '';
      let timedOut = false;
      const timer = setTimeout(() => {
        timedOut = true;
        child.kill();
      }, timeoutMs);
      child.stdout.on('data', (chunk: Buffer) => { stdout += chunk.toString('utf8'); });
      child.stderr.on('data', (chunk: Buffer) => { stderr += chunk.toString('utf8'); });
      child.on('error', (error) => {
        clearTimeout(timer);
        resolvePromise({ ok: false, output: `Failed to run command: ${error.message}` });
      });
      child.on('close', (code) => {
        clearTimeout(timer);
        const exitCode = code ?? -1;
        const parts = [
          stdout && `stdout:\n${stdout.slice(0, MAX_OUTPUT_CHARS)}`,
          stderr && `stderr:\n${stderr.slice(0, MAX_OUTPUT_CHARS)}`,
          timedOut && `[命令超时（${timeoutMs}ms），已被终止]`,
        ].filter(Boolean);
        resolvePromise({
          ok: !timedOut && exitCode === 0,
          exitCode,
          output: parts.join('\n') || '(no output)',
        });
      });
    });
  },
};
