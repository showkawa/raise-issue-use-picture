import type { AgentConfig, PermissionMode } from '../types.js';
import type { ToolCall } from './protocol.js';
import type { ToolRisk } from './tools/types.js';
import { classifyCommand } from './command-classify.js';

export interface PermissionDecision {
  allowed: boolean;
  reason?: string;
}

/** Asked when a call needs interactive confirmation; return true to allow. */
export type ConfirmHandler = (call: ToolCall, reason: string) => Promise<boolean>;

export interface PermissionGateOptions {
  mode?: PermissionMode;
  confirm?: ConfirmHandler;
}

function commandTextOf(call: ToolCall): string {
  if (call.name === 'run_command' && typeof call.args.command === 'string') {
    return call.args.command;
  }
  if (call.name === 'git') {
    const parts = [call.args.subcommand, ...(Array.isArray(call.args.args) ? call.args.args : [])];
    return `git ${parts.filter((part) => typeof part === 'string').join(' ')}`;
  }
  return '';
}

export class PermissionGate {
  private mode: PermissionMode;
  private config: AgentConfig;
  private confirm?: ConfirmHandler;

  constructor(config: AgentConfig, options: PermissionGateOptions = {}) {
    this.config = config;
    this.mode = options.mode ?? config.permissionMode;
    this.confirm = options.confirm;
  }

  async check(call: ToolCall, risk: ToolRisk): Promise<PermissionDecision> {
    const commandText = commandTextOf(call);
    const denyHit = commandText
      ? this.config.denyCommands.find((needle) => commandText.includes(needle))
      : undefined;

    if (denyHit) {
      return this.askUser(call, `命令命中 denyCommands 规则 "${denyHit}"`);
    }
    if (risk === 'destructive') {
      return this.askUser(call, '该操作被标记为 destructive');
    }
    const classified = commandText ? classifyCommand(commandText) : { destructive: false };
    if (classified.destructive) {
      return this.askUser(call, `命令被判定为破坏性操作：${classified.reason}`);
    }

    switch (this.mode) {
      case 'yolo':
        return { allowed: true };
      case 'allowlist': {
        if (risk === 'read') return { allowed: true };
        if (risk === 'write') return { allowed: true };
        const allowHit = commandText
          && this.config.allowCommands.some((needle) => commandText.startsWith(needle));
        if (allowHit) return { allowed: true };
        return this.askUser(call, 'allowlist 模式下 exec 类操作需要确认');
      }
      case 'ask':
        return this.askUser(call, 'ask 模式下所有操作都需要确认');
    }
  }

  private async askUser(call: ToolCall, reason: string): Promise<PermissionDecision> {
    if (!this.confirm) {
      return { allowed: false, reason: `${reason}（非交互环境，已拒绝）` };
    }
    const approved = await this.confirm(call, reason);
    return approved
      ? { allowed: true }
      : { allowed: false, reason: `${reason}（用户拒绝）` };
  }
}
