import { describe, expect, it } from 'vitest';
import { normalizeCliArgv } from '../src/cli/argv.js';

const prefix = ['node', 'dist/cli/index.js'];

describe('normalizeCliArgv', () => {
  it('treats a direct question as the ask command', () => {
    expect(normalizeCliArgv([...prefix, 'write', 'a', 'function']))
      .toEqual([...prefix, 'ask', 'write', 'a', 'function']);
  });

  it('preserves explicit commands', () => {
    expect(normalizeCliArgv([...prefix, 'prd', 'demo']))
      .toEqual([...prefix, 'prd', 'demo']);
    expect(normalizeCliArgv([...prefix, 'review', 'src/index.ts']))
      .toEqual([...prefix, 'review', 'src/index.ts']);
  });

  it('inserts ask after global options', () => {
    expect(normalizeCliArgv([
      ...prefix,
      '--config',
      'custom.yaml',
      '--no-stream',
      'hello',
    ])).toEqual([
      ...prefix,
      '--config',
      'custom.yaml',
      '--no-stream',
      'ask',
      'hello',
    ]);
  });

  it('does not reinterpret help or version options', () => {
    expect(normalizeCliArgv([...prefix, '--help'])).toEqual([...prefix, '--help']);
    expect(normalizeCliArgv([...prefix, '--version'])).toEqual([...prefix, '--version']);
  });

  it('supports a question beginning with a dash after --', () => {
    expect(normalizeCliArgv([...prefix, '--', '-question']))
      .toEqual([...prefix, 'ask', '--', '-question']);
  });
});
