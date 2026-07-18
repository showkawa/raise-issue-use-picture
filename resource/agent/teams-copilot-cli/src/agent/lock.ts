import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'fs';
import { join } from 'path';

/** A lock considered abandoned after this long, even if a pid is recorded. */
const LOCK_STALE_MS = 6 * 60 * 60 * 1000;

export interface AgentLock {
  release(): void;
}

interface LockInfo {
  pid: number;
  ts: number;
}

function defaultIsAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    // EPERM means the process exists but we can't signal it — still alive.
    return (error as NodeJS.ErrnoException).code === 'EPERM';
  }
}

function readLock(path: string): LockInfo | null {
  try {
    const parsed = JSON.parse(readFileSync(path, 'utf8')) as Partial<LockInfo>;
    if (typeof parsed.pid === 'number' && typeof parsed.ts === 'number') {
      return { pid: parsed.pid, ts: parsed.ts };
    }
  } catch {
    // corrupt lock is treated as absent
  }
  return null;
}

/**
 * Acquires an exclusive project-root lock so two agent runs can't clobber the same
 * worktree concurrently. Stale or dead-pid locks are reclaimed.
 */
export function acquireLock(
  projectRoot: string,
  options: { now?: number; isAlive?: (pid: number) => boolean } = {},
): AgentLock {
  const now = options.now ?? Date.now();
  const isAlive = options.isAlive ?? defaultIsAlive;
  const dir = join(projectRoot, '.teams-copilot');
  const lockPath = join(dir, 'agent.lock');
  mkdirSync(dir, { recursive: true });

  if (existsSync(lockPath)) {
    const info = readLock(lockPath);
    const held = info && isAlive(info.pid) && now - info.ts < LOCK_STALE_MS;
    if (held) {
      throw new Error(
        `另一个 agent 正在此项目运行（pid ${info!.pid}，锁文件 ${lockPath}）。`
        + '若确认没有其它进程，删除该文件后重试。',
      );
    }
  }

  writeFileSync(lockPath, JSON.stringify({ pid: process.pid, ts: now }), 'utf8');
  let released = false;
  return {
    release() {
      if (released) return;
      released = true;
      try {
        rmSync(lockPath, { force: true });
      } catch {
        // best effort
      }
    },
  };
}
