import { afterEach, describe, expect, it } from 'vitest';
import { mkdtempSync, rmSync, writeFileSync } from 'fs';
import { join } from 'path';
import { tmpdir } from 'os';
import {
  buildCodePrompt,
  inferCodeLanguage,
  readTextFile,
  writeTextOutput,
} from '../src/cli/prompt-content.js';

const directories: string[] = [];

afterEach(() => {
  for (const directory of directories.splice(0)) {
    rmSync(directory, { recursive: true, force: true });
  }
});

describe('buildCodePrompt', () => {
  it('wraps text in a language-specific Markdown code block', () => {
    expect(buildCodePrompt('Explain this code', 'const value = 1;', 'typescript'))
      .toBe('Explain this code\n\n```typescript\nconst value = 1;\n```');
  });

  it('uses a longer fence when the content contains backticks', () => {
    const prompt = buildCodePrompt('Explain', 'const block = ```code```;', 'javascript');
    expect(prompt).toContain('````javascript\n');
    expect(prompt).toContain('\n````');
  });

  it('sanitizes the language marker', () => {
    expect(buildCodePrompt('Explain', 'value', 'ts\n```'))
      .toContain('```ts\n');
  });

  it('rejects binary content', () => {
    expect(() => buildCodePrompt('Explain', 'a\0b')).toThrow('binary data');
  });
});

describe('file text helpers', () => {
  it('infers common code languages from the extension', () => {
    expect(inferCodeLanguage('src/example.ts')).toBe('typescript');
    expect(inferCodeLanguage('script.py')).toBe('python');
    expect(inferCodeLanguage('unknown.code')).toBe('text');
  });

  it('reads a local text file', () => {
    const directory = mkdtempSync(join(tmpdir(), 'tcc-prompt-'));
    directories.push(directory);
    const filePath = join(directory, 'example.ts');
    writeFileSync(filePath, 'export const value = 1;\n');

    expect(readTextFile(filePath)).toBe('export const value = 1;\n');
  });

  it('reports a missing local text file clearly', () => {
    expect(() => readTextFile('/missing/tcc-example.ts'))
      .toThrow('Text file not found');
  });

  it('writes text output and creates parent directories', () => {
    const directory = mkdtempSync(join(tmpdir(), 'tcc-output-'));
    directories.push(directory);
    const outputPath = join(directory, 'nested', 'answer.md');

    const resolvedPath = writeTextOutput(outputPath, 'answer\n');

    expect(resolvedPath).toBe(outputPath);
    expect(readTextFile(outputPath)).toBe('answer\n');
  });
});
