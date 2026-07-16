import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { AgentMaxIterationsError, AgentProtocolError, runAgent, sendChunked } from '../src/agent/loop.js';
import { PermissionGate } from '../src/agent/permissions.js';
import { createDefaultRegistry } from '../src/agent/tools/registry.js';
import type { WorkspaceInfo } from '../src/agent/system-prompt.js';
import { MockProvider } from '../src/provider/mock.js';
import type { AgentConfig } from '../src/types.js';

let root: string;
let workspace: WorkspaceInfo;

const config: AgentConfig = {
  permissionMode: 'yolo',
  maxIterations: 10,
  maxContinuations: 4,
  maxTurnsPerConversation: 30,
  minSendIntervalMs: 0,
  maxMessageChars: 8000,
  denyCommands: ['git push'],
  allowCommands: [],
};

beforeEach(() => {
  root = mkdtempSync(join(tmpdir(), 'tcc-loop-'));
  workspace = { projectRoot: root, repoMap: 'app.txt', os: 'test', shell: 'test' };
});

afterEach(() => {
  rmSync(root, { recursive: true, force: true });
});

function deps(provider: MockProvider, overrides: Partial<AgentConfig> = {}) {
  const merged = { ...config, ...overrides };
  return {
    provider,
    registry: createDefaultRegistry(),
    gate: new PermissionGate(merged),
    workspace,
    config: merged,
  };
}

const toolBlock = (name: string, args: Record<string, unknown>) =>
  `<<<TOOL name="${name}"\n${JSON.stringify(args)}\n>>>`;

describe('runAgent (MockProvider integration)', () => {
  it('runs a read -> edit -> done flow against real files', async () => {
    writeFileSync(join(root, 'app.txt'), 'version = 1\n');
    const provider = new MockProvider([
      'OK',
      toolBlock('read_file', { path: 'app.txt' }),
      toolBlock('edit_file', { path: 'app.txt', old: 'version = 1', new: 'version = 2' }),
      '<<<DONE\n已把 version 升到 2。\n>>>',
    ]);
    const result = await runAgent('bump version', { ...deps(provider) });
    expect(result.summary).toContain('version 升到 2');
    expect(readFileSync(join(root, 'app.txt'), 'utf8')).toBe('version = 2\n');
    expect(result.actions.some((action) => action.startsWith('edit_file'))).toBe(true);
    // 第一条消息是协议注入，第二条是任务
    expect(provider.sent[0]).toContain('工具清单');
    expect(provider.sent[1]).toContain('bump version');
    // 工具结果以 RESULT 块回灌
    expect(provider.sent[2]).toContain('<<<RESULT name="read_file"');
  });

  it('feeds tool failures back and lets the model recover', async () => {
    writeFileSync(join(root, 'app.txt'), 'hello world\n');
    const provider = new MockProvider([
      'OK',
      toolBlock('edit_file', { path: 'app.txt', old: 'helo world', new: 'x' }),
      toolBlock('edit_file', { path: 'app.txt', old: 'hello world', new: 'hi world' }),
      '<<<DONE\n完成\n>>>',
    ]);
    await runAgent('fix', { ...deps(provider) });
    expect(provider.sent[2]).toContain('ok="false"');
    expect(readFileSync(join(root, 'app.txt'), 'utf8')).toBe('hi world\n');
  });

  it('sends a correction message on malformed replies, then errors after the limit', async () => {
    const provider = new MockProvider([], {
      respond: () => '我不想遵守协议。',
    });
    await expect(runAgent('task', { ...deps(provider) })).rejects.toThrow(AgentProtocolError);
    const corrections = provider.sent.filter((message) => message.includes('无法解析'));
    expect(corrections.length).toBe(2);
  });

  it('denies gated calls and reports the denial to the model', async () => {
    const provider = new MockProvider([
      'OK',
      toolBlock('run_command', { command: 'git push origin main' }),
      '<<<DONE\n放弃 push。\n>>>',
    ]);
    await runAgent('push it', { ...deps(provider) });
    expect(provider.sent[2]).toContain('操作被拒绝');
  });

  it('throws AgentMaxIterationsError when the model never finishes', async () => {
    const provider = new MockProvider([], {
      respond: (_message, index) => (index === 0 ? 'OK' : toolBlock('glob', { pattern: '*.txt' })),
    });
    await expect(runAgent('loop forever', { ...deps(provider, { maxIterations: 3 }) }))
      .rejects.toThrow(AgentMaxIterationsError);
  });

  it('rotates the session when it becomes unhealthy and replays a progress summary', async () => {
    let sessionCount = 0;
    const provider = new MockProvider([], {
      respond: (message) => {
        if (message.includes('工具清单')) {
          sessionCount += 1;
          return 'OK';
        }
        if (message.includes('会话已重建')) return '<<<DONE\n重建后完成\n>>>';
        if (message.includes('## 任务')) return toolBlock('glob', { pattern: '*.txt' });
        return '<<<DONE\n完成\n>>>';
      },
    });
    const base = deps(provider);
    // 执行第一批工具后标记会话不健康
    const originalCreate = provider.createSession.bind(provider);
    let created = 0;
    provider.createSession = async () => {
      created += 1;
      const session = await originalCreate();
      if (created === 1) {
        const originalSend = session.send.bind(session);
        session.send = async (message, options) => {
          const result = await originalSend(message, options);
          if (message.includes('## 任务')) provider.markUnhealthy();
          return result;
        };
      } else {
        provider.markHealthy();
      }
      return session;
    };
    const result = await runAgent('task', base);
    expect(created).toBe(2);
    expect(sessionCount).toBe(2);
    expect(result.summary).toContain('重建后完成');
    expect(provider.sent.some((message) => message.includes('进度摘要'))).toBe(true);
  });
});

describe('sendChunked', () => {
  it('passes short messages through unchanged', async () => {
    const provider = new MockProvider(['reply']);
    const session = await provider.createSession();
    const result = await sendChunked(session, 'short', 100);
    expect(result.text).toBe('reply');
    expect(provider.sent).toEqual(['short']);
  });

  it('splits long messages into parts with part markers', async () => {
    const provider = new MockProvider(['OK', 'OK', 'final']);
    const session = await provider.createSession();
    const message = 'x'.repeat(1200);
    const result = await sendChunked(session, message, 500);
    expect(result.text).toBe('final');
    expect(provider.sent).toHaveLength(3);
    expect(provider.sent[0]).toContain('[part 1/3]');
    expect(provider.sent[0]).toContain('只回复 OK');
    expect(provider.sent[2]).toContain('[part 3/3]');
    expect(provider.sent.map((part) => part.replace(/^\[part [^\]]+\][^\n]*\n/, '')).join('')).toBe(message);
  });
});
