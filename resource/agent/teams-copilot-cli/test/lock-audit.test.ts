import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { acquireLock } from '../src/agent/lock.js';
import { createAuditLogger } from '../src/agent/audit.js';

let root: string;

beforeEach(() => {
  root = mkdtempSync(join(tmpdir(), 'tcc-lock-'));
});

afterEach(() => {
  rmSync(root, { recursive: true, force: true });
});

describe('acquireLock', () => {
  it('refuses a second acquire while the lock is held, then allows after release', () => {
    const lock = acquireLock(root);
    expect(() => acquireLock(root)).toThrow('另一个 agent');
    lock.release();
    const again = acquireLock(root);
    again.release();
  });

  it('reclaims a stale lock', () => {
    mkdirSync(join(root, '.teams-copilot'), { recursive: true });
    writeFileSync(
      join(root, '.teams-copilot', 'agent.lock'),
      JSON.stringify({ pid: process.pid, ts: 0 }),
      'utf8',
    );
    const lock = acquireLock(root, { now: 1000 * 60 * 60 * 24 });
    lock.release();
  });

  it('reclaims a lock whose process is gone', () => {
    mkdirSync(join(root, '.teams-copilot'), { recursive: true });
    writeFileSync(
      join(root, '.teams-copilot', 'agent.lock'),
      JSON.stringify({ pid: 424242, ts: Date.now() }),
      'utf8',
    );
    const lock = acquireLock(root, { isAlive: () => false });
    lock.release();
  });
});

describe('createAuditLogger', () => {
  it('appends one JSON line per entry', () => {
    const log = createAuditLogger(root, () => new Date('2024-01-01T00:00:00Z'));
    log({ tool: 'run_command', target: 'npm test', allowed: true, ok: true, exitCode: 0 });
    log({ tool: 'run_command', target: 'git push', allowed: false, reason: 'destructive' });
    const lines = readFileSync(join(root, '.teams-copilot', 'audit.log'), 'utf8').trim().split('\n');
    expect(lines).toHaveLength(2);
    expect(JSON.parse(lines[0])).toMatchObject({ tool: 'run_command', allowed: true, ok: true });
    expect(JSON.parse(lines[1])).toMatchObject({ allowed: false, reason: 'destructive' });
    expect(JSON.parse(lines[0]).ts).toBe('2024-01-01T00:00:00.000Z');
  });
});
