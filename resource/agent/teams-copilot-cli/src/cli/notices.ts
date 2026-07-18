import { existsSync, mkdirSync, writeFileSync } from 'fs';
import { join } from 'path';
import { homedir } from 'os';
import type { PermissionMode } from '../types.js';

const STATE_DIR = join(homedir(), '.teams-copilot');
const EGRESS_ACK = join(STATE_DIR, '.egress-ack');

function emit(message: string): void {
  process.stderr.write(`${message}\n`);
}

/** Prints a prominent warning whenever the dangerous YOLO mode is active. */
export function warnYoloMode(mode: PermissionMode): void {
  if (mode !== 'yolo') return;
  emit('');
  emit('⚠️  YOLO 模式：所有工具调用（含 run_command）将自动执行，不再逐项确认。');
  emit('   模型输出可能被提示注入污染，YOLO 下等同于远程命令执行风险。');
  emit('   仅在完全信任任务与仓库内容时使用；默认建议用 allowlist 模式。');
  emit('');
}

/**
 * Prints a one-time notice that `code`/`implement` send repository contents to
 * Microsoft 365 Copilot, then records acknowledgement so it isn't shown again.
 */
export function noticeEgressOnce(): void {
  if (existsSync(EGRESS_ACK)) return;
  emit('');
  emit('ℹ️  该模式会把仓库源码、命令输出和目录结构发送到 Microsoft 365 Copilot（企业租户）。');
  emit('   常见凭据模式会被自动打码，.env/*.pem/id_rsa 等敏感文件默认拒读。');
  emit('   此提示仅显示一次。');
  emit('');
  try {
    mkdirSync(STATE_DIR, { recursive: true });
    writeFileSync(EGRESS_ACK, new Date().toISOString(), 'utf8');
  } catch {
    // Non-fatal: if we cannot persist the ack, the notice just shows again next time.
  }
}

/** Resolves the effective permission mode from CLI flags (--yolo/--ask/--permission-mode). */
export function resolvePermissionMode(
  current: PermissionMode,
  flags: { yolo?: boolean; ask?: boolean; permissionMode?: string },
): PermissionMode {
  if (flags.permissionMode) {
    if (!['yolo', 'allowlist', 'ask'].includes(flags.permissionMode)) {
      throw new Error(`Invalid permission mode: ${flags.permissionMode}`);
    }
    return flags.permissionMode as PermissionMode;
  }
  if (flags.yolo && flags.ask) {
    throw new Error('Cannot combine --yolo and --ask');
  }
  if (flags.yolo) return 'yolo';
  if (flags.ask) return 'ask';
  return current;
}
