import { afterEach, describe, expect, it } from 'vitest';
import { existsSync, unlinkSync, writeFileSync } from 'fs';
import { join } from 'path';
import { tmpdir } from 'os';
import { loadConfig } from '../src/config.js';

const tempFiles: string[] = [];

function writeConfig(content: string): string {
  const path = join(tmpdir(), `tcc-config-${Date.now()}-${Math.random().toString(36).slice(2)}.yaml`);
  writeFileSync(path, content, 'utf8');
  tempFiles.push(path);
  return path;
}

afterEach(() => {
  for (const f of tempFiles.splice(0)) {
    if (existsSync(f)) unlinkSync(f);
  }
});

describe('loadConfig', () => {
  it('returns proxy defaults when no config file exists', () => {
    const config = loadConfig(join(tmpdir(), 'does-not-exist-tcc.yaml'));
    expect(config.provider).toBe('proxy');
    expect(config.proxy).toEqual({
      baseUrl: 'http://127.0.0.1:8000/v1',
      model: 'm365-copilot',
      apiKey: 'unused',
      timeoutMs: 120000,
    });
  });

  it('merges proxy overrides from the file over the defaults', () => {
    const path = writeConfig('proxy:\n  baseUrl: http://localhost:9000/v1\n  model: custom\n');
    const config = loadConfig(path);
    expect(config.proxy.baseUrl).toBe('http://localhost:9000/v1');
    expect(config.proxy.model).toBe('custom');
    expect(config.proxy.apiKey).toBe('unused');
    expect(config.proxy.timeoutMs).toBe(120000);
  });

  it('tolerates and ignores legacy browser/copilot/agent keys', () => {
    const path = writeConfig('provider: proxy\nbrowser:\n  port: 9222\ncopilot:\n  copilotUrl: x\nagent:\n  maxIterations: 3\n');
    const config = loadConfig(path);
    expect(config).toEqual({ provider: 'proxy', proxy: expect.objectContaining({ model: 'm365-copilot' }) });
  });

  it('rejects an unknown provider', () => {
    const path = writeConfig('provider: copilot-web\n');
    expect(() => loadConfig(path)).toThrow(/Invalid field: provider/);
  });

  it('rejects a non-positive proxy timeout', () => {
    const path = writeConfig('proxy:\n  timeoutMs: 0\n');
    expect(() => loadConfig(path)).toThrow(/Invalid field: proxy.timeoutMs/);
  });
});
