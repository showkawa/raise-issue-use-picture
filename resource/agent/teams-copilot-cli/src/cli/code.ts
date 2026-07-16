import { createInterface } from 'readline';
import type { PermissionMode } from '../types.js';
import { loadConfig } from '../provider/copilot-web/config.js';
import { createProvider } from '../provider/factory.js';
import { runAgent } from '../agent/loop.js';
import { PermissionGate, type ConfirmHandler } from '../agent/permissions.js';
import { createDefaultRegistry } from '../agent/tools/registry.js';
import { buildWorkspaceInfo, expandFileReferences } from '../context/workspace.js';
import type { CommandOpts } from './ask.js';
import { browserFlagsFromOptions } from './utils.js';

export interface CodeCommandOpts extends CommandOpts {
  permissionMode?: string;
  maxIterations?: string;
}

function reportStatus(message: string): void {
  process.stderr.write(`[tcc] ${message}\n`);
}

function interactiveConfirm(): ConfirmHandler | undefined {
  if (!process.stdin.isTTY) return undefined;
  return async (call, reason) => {
    const rl = createInterface({ input: process.stdin, output: process.stderr });
    try {
      const answer = await new Promise<string>((resolvePromise) => {
        rl.question(
          `[tcc] 需要确认（${reason}）\n  ${call.name} ${JSON.stringify(call.args).slice(0, 200)}\n  允许执行? [y/N] `,
          resolvePromise,
        );
      });
      return /^y(es)?$/i.test(answer.trim());
    } finally {
      rl.close();
    }
  };
}

export async function codeCommand(task: string, opts: CodeCommandOpts): Promise<void> {
  const config = loadConfig(opts.config);
  config.browser = { ...config.browser, ...browserFlagsFromOptions(opts) };
  if (opts.permissionMode) {
    if (!['yolo', 'allowlist', 'ask'].includes(opts.permissionMode)) {
      throw new Error(`Invalid permission mode: ${opts.permissionMode}`);
    }
    config.agent.permissionMode = opts.permissionMode as PermissionMode;
  }
  if (opts.maxIterations) {
    const parsed = Number.parseInt(opts.maxIterations, 10);
    if (!Number.isInteger(parsed) || parsed < 1) {
      throw new Error(`Invalid max iterations: ${opts.maxIterations}`);
    }
    config.agent.maxIterations = parsed;
  }

  const workspace = buildWorkspaceInfo();
  const expandedTask = expandFileReferences(task, workspace.projectRoot);
  const provider = createProvider(config, reportStatus);
  await provider.init();
  try {
    const result = await runAgent(expandedTask, {
      provider,
      registry: createDefaultRegistry(),
      gate: new PermissionGate(config.agent, { confirm: interactiveConfirm() }),
      workspace,
      config: config.agent,
      ui: {
        onStatus: reportStatus,
        onCommentary: (text) => process.stderr.write(`\n${text}\n`),
        onToolCall: (call) => process.stderr.write(`[tcc] → ${call.name} ${JSON.stringify(call.args).slice(0, 160)}\n`),
        onToolResult: (call, toolResult) => process.stderr.write(`[tcc] ← ${call.name} ${toolResult.ok ? 'ok' : 'failed'}\n`),
      },
    });
    process.stdout.write(`\n完成（${result.iterations} 轮迭代）：${result.summary}\n`);
  } finally {
    await provider.close();
  }
}
