import { describe, expect, it } from 'vitest';
import { PermissionGate } from '../src/agent/permissions.js';
import type { ToolCall } from '../src/agent/protocol.js';
import type { AgentConfig } from '../src/types.js';

const baseConfig: AgentConfig = {
  permissionMode: 'yolo',
  maxIterations: 25,
  maxContinuations: 4,
  maxTurnsPerConversation: 30,
  minSendIntervalMs: 0,
  maxMessageChars: 8000,
  denyCommands: ['rm -rf', 'git push'],
  allowCommands: ['npm test'],
};

function call(name: string, args: Record<string, unknown>): ToolCall {
  return { name, args, raw: '' };
}

describe('PermissionGate', () => {
  it('yolo allows read/write/exec directly', async () => {
    const gate = new PermissionGate(baseConfig);
    expect((await gate.check(call('read_file', { path: 'a' }), 'read')).allowed).toBe(true);
    expect((await gate.check(call('write_file', { path: 'a', content: '' }), 'write')).allowed).toBe(true);
    expect((await gate.check(call('run_command', { command: 'npm test' }), 'exec')).allowed).toBe(true);
  });

  it('denyCommands requires confirmation even in yolo, denied without a confirmer', async () => {
    const gate = new PermissionGate(baseConfig);
    const decision = await gate.check(call('run_command', { command: 'rm -rf /' }), 'exec');
    expect(decision.allowed).toBe(false);
    expect(decision.reason).toContain('denyCommands');
  });

  it('denyCommands also covers the git tool arguments', async () => {
    const gate = new PermissionGate(baseConfig);
    const decision = await gate.check(
      call('git', { subcommand: 'push', args: ['origin', 'main'] }),
      'exec',
    );
    expect(decision.allowed).toBe(false);
  });

  it('token-level classifier flags destructive commands outside denyCommands, even in yolo', async () => {
    const gate = new PermissionGate(baseConfig);
    const decision = await gate.check(
      call('run_command', { command: 'echo ok && Remove-Item -Recurse dist' }),
      'exec',
    );
    expect(decision.allowed).toBe(false);
    expect(decision.reason).toContain('破坏性');
  });

  it('asks the confirm handler and honors approval', async () => {
    let asked = '';
    const gate = new PermissionGate(baseConfig, {
      confirm: async (_call, reason) => {
        asked = reason;
        return true;
      },
    });
    const decision = await gate.check(call('run_command', { command: 'git push' }), 'exec');
    expect(decision.allowed).toBe(true);
    expect(asked).toContain('denyCommands');
  });

  it('allowlist mode auto-allows read/write and allowlisted exec only', async () => {
    const gate = new PermissionGate({ ...baseConfig, permissionMode: 'allowlist' });
    expect((await gate.check(call('read_file', { path: 'a' }), 'read')).allowed).toBe(true);
    expect((await gate.check(call('write_file', { path: 'a', content: '' }), 'write')).allowed).toBe(true);
    expect((await gate.check(call('run_command', { command: 'npm test -- x' }), 'exec')).allowed).toBe(true);
    expect((await gate.check(call('run_command', { command: 'del x' }), 'exec')).allowed).toBe(false);
  });

  it('ask mode requires confirmation for everything', async () => {
    const gate = new PermissionGate({ ...baseConfig, permissionMode: 'ask' });
    expect((await gate.check(call('read_file', { path: 'a' }), 'read')).allowed).toBe(false);
    const approving = new PermissionGate(
      { ...baseConfig, permissionMode: 'ask' },
      { confirm: async () => true },
    );
    expect((await approving.check(call('read_file', { path: 'a' }), 'read')).allowed).toBe(true);
  });

  it('mode override wins over config', async () => {
    const gate = new PermissionGate(baseConfig, { mode: 'ask' });
    expect((await gate.check(call('read_file', { path: 'a' }), 'read')).allowed).toBe(false);
  });
});
