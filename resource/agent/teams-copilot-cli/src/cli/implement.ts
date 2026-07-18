import { execFileSync } from 'child_process';
import { readFileSync, writeFileSync } from 'fs';
import { resolve } from 'path';
import { loadConfig } from '../provider/copilot-web/config.js';
import { createProvider } from '../provider/factory.js';
import { runAgent } from '../agent/loop.js';
import { PermissionGate } from '../agent/permissions.js';
import { createDefaultRegistry } from '../agent/tools/registry.js';
import { acquireLock } from '../agent/lock.js';
import { createAuditLogger } from '../agent/audit.js';
import { findMalformedTaskLines, markTaskDone, parseTasks, type TaskItem } from '../agent/tasks-file.js';
import { buildWorkspaceInfo } from '../context/workspace.js';
import type { CommandOpts } from './ask.js';
import { browserFlagsFromOptions } from './utils.js';
import { noticeEgressOnce, resolvePermissionMode, warnYoloMode } from './notices.js';

export interface ImplementCommandOpts extends CommandOpts {
  tasks?: string;
  task?: string;
  continueOnFailure?: boolean;
  commit?: boolean;
  permissionMode?: string;
  yolo?: boolean;
  ask?: boolean;
  allowDirty?: boolean;
  maxIterations?: string;
}

/** Refuses to start on a dirty worktree unless --allow-dirty, so the agent never
 *  commits or clobbers the user's uncommitted changes (ADR-0005). */
export function ensureCleanWorktree(changed: string[], allowDirty: boolean): void {
  if (changed.length > 0 && !allowDirty) {
    throw new Error(
      `工作树不干净（${changed.length} 个未提交改动）。请先提交/暂存，`
      + '或加 --allow-dirty 在脏工作树上运行（此时自动 commit 会被禁用）。',
    );
  }
}

function reportStatus(message: string): void {
  process.stderr.write(`[tcc] ${message}\n`);
}

function git(projectRoot: string, args: string[]): string {
  return execFileSync('git', args, { cwd: projectRoot, encoding: 'utf8' });
}

function changedFiles(projectRoot: string): string[] {
  return git(projectRoot, ['status', '--porcelain'])
    .split(/\r?\n/)
    .filter(Boolean)
    .map((line) => line.slice(3).trim())
    .filter(Boolean)
    .map((path) => (path.includes(' -> ') ? path.split(' -> ')[1] : path))
    .map((path) => path.replace(/^"|"$/g, ''));
}

export async function implementCommand(opts: ImplementCommandOpts): Promise<void> {
  const workspace = buildWorkspaceInfo();
  const tasksPath = resolve(workspace.projectRoot, opts.tasks ?? 'output/TASKS.md');
  const config = loadConfig(opts.config);
  config.browser = { ...config.browser, ...browserFlagsFromOptions(opts) };
  config.agent.permissionMode = resolvePermissionMode(config.agent.permissionMode, opts);
  if (opts.maxIterations) {
    const parsed = Number.parseInt(opts.maxIterations, 10);
    if (!Number.isInteger(parsed) || parsed < 1) {
      throw new Error(`Invalid max iterations: ${opts.maxIterations}`);
    }
    config.agent.maxIterations = parsed;
  }

  let content = readFileSync(tasksPath, 'utf8');
  const malformed = findMalformedTaskLines(content);
  if (malformed.length > 0) {
    process.stderr.write(
      `[tcc] ${tasksPath} 有 ${malformed.length} 行疑似任务但格式不合规，已跳过（未静默漏掉，请修正为 "- [ ] T1: ..."）：\n`
      + malformed.map((item) => `  L${item.line + 1}: ${item.text}`).join('\n') + '\n',
    );
  }
  const all = parseTasks(content);
  if (all.length === 0) {
    throw new Error(`No checkbox tasks found in ${tasksPath}. Expected lines like "- [ ] T1: ..."`);
  }
  let selected: TaskItem[];
  if (opts.task) {
    const task = all.find((item) => item.id === opts.task);
    if (!task) throw new Error(`Task "${opts.task}" not found. Available: ${all.map((item) => item.id).join(', ')}`);
    if (task.done) throw new Error(`Task "${opts.task}" is already done`);
    selected = [task];
  } else {
    selected = all.filter((task) => !task.done);
    if (selected.length === 0) {
      process.stdout.write('所有任务均已完成。\n');
      return;
    }
  }

  const initialDirty = changedFiles(workspace.projectRoot);
  ensureCleanWorktree(initialDirty, opts.allowDirty === true);
  let autoCommit = opts.commit === true;
  if (autoCommit && initialDirty.length > 0) {
    process.stderr.write('[tcc] 工作树不干净，已禁用自动 commit（避免把你已有的改动一并提交）。\n');
    autoCommit = false;
  }

  noticeEgressOnce();
  warnYoloMode(config.agent.permissionMode);

  const lock = acquireLock(workspace.projectRoot);
  const audit = createAuditLogger(workspace.projectRoot);
  const provider = createProvider(config, reportStatus);
  await provider.init();
  const registry = createDefaultRegistry();
  const gate = new PermissionGate(config.agent);
  let failed = 0;
  try {
    for (const task of selected) {
      process.stdout.write(`\n=== 任务 ${task.id}: ${task.description}\n`);
      try {
        const result = await runAgent(
          `完成以下开发任务（完成后必须运行测试/类型检查验证通过再 DONE）：\n${task.description}`,
          { provider, registry, gate, workspace, config: config.agent, audit, ui: { onStatus: reportStatus } },
        );
        process.stdout.write(`任务 ${task.id} 完成：${result.summary}\n`);
        content = readFileSync(tasksPath, 'utf8');
        const fresh = parseTasks(content).find((item) => item.id === task.id && !item.done);
        if (fresh) {
          content = markTaskDone(content, fresh);
          writeFileSync(tasksPath, content, 'utf8');
        }
        if (autoCommit) {
          const files = changedFiles(workspace.projectRoot);
          for (const file of files) {
            git(workspace.projectRoot, ['add', '--', file]);
          }
          if (files.length > 0) {
            git(workspace.projectRoot, ['commit', '-m', `feat(${task.id}): ${task.description.slice(0, 72)}`]);
            process.stdout.write(`已提交 ${files.length} 个文件。\n`);
          }
        }
      } catch (error) {
        failed += 1;
        const message = error instanceof Error ? error.message : String(error);
        process.stderr.write(`任务 ${task.id} 失败：${message}\n`);
        if (!opts.continueOnFailure) {
          process.stderr.write('已停止（checkbox 保持未勾选；半成品改动保留在工作树中、未 commit、未 stash）。\n');
          process.stderr.write('请人工检查：`git status` 查看改动，`git diff` 查看内容，`git checkout -p` 可选择性回退。\n');
          process.stderr.write('确认无误后可加 --continue-on-failure 跳过失败任务继续。\n');
          break;
        }
      }
    }
  } finally {
    await provider.close();
    lock.release();
  }
  if (failed > 0) {
    process.exitCode = 1;
  }
}
