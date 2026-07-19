import { afterEach, describe, expect, it } from 'vitest';
import { existsSync, readFileSync, rmSync } from 'fs';
import { join } from 'path';
import { tmpdir } from 'os';
import { Readable } from 'stream';
import { readDelimitedQuestion, writeTextOutput } from '../src/cli/input.js';

const tempPaths: string[] = [];

afterEach(() => {
  for (const p of tempPaths.splice(0)) {
    if (existsSync(p)) rmSync(p, { recursive: true, force: true });
  }
});

function stream(text: string): Readable {
  return Readable.from([text]);
}

describe('readDelimitedQuestion', () => {
  it('reads lines until a lone terminator', async () => {
    const result = await readDelimitedQuestion(stream('line one\nline two\n@\n'));
    expect(result).toBe('line one\nline two');
  });

  it('throws when the terminator is missing', async () => {
    await expect(readDelimitedQuestion(stream('no terminator here\n'))).rejects.toThrow(/must end with @/);
  });

  it('throws when the prompt is empty', async () => {
    await expect(readDelimitedQuestion(stream('@\n'))).rejects.toThrow(/empty/);
  });
});

describe('writeTextOutput', () => {
  it('writes content and creates missing parent directories', () => {
    const dir = join(tmpdir(), `tcc-out-${Date.now()}`);
    tempPaths.push(dir);
    const target = join(dir, 'nested', 'summary.md');
    const written = writeTextOutput(target, 'hello\n');
    expect(written).toBe(target);
    expect(readFileSync(target, 'utf8')).toBe('hello\n');
  });
});
