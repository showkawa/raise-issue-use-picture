import { afterEach, describe, expect, it } from 'vitest';
import { mkdtempSync, rmSync, writeFileSync } from 'fs';
import { join } from 'path';
import { tmpdir } from 'os';
import { prepareCodeUpload } from '../src/runtime/code-file-uploader.js';

const directories: string[] = [];

function createFile(name: string, content: string | Buffer): string {
  const directory = mkdtempSync(join(tmpdir(), 'tcc-upload-'));
  directories.push(directory);
  const filePath = join(directory, name);
  writeFileSync(filePath, content);
  return filePath;
}

afterEach(() => {
  for (const directory of directories.splice(0)) {
    rmSync(directory, { recursive: true, force: true });
  }
});

describe('prepareCodeUpload', () => {
  it('uploads supported code extensions directly', () => {
    const filePath = createFile('example.py', 'print("hello")\n');
    const prepared = prepareCodeUpload(filePath);

    expect(prepared.aliased).toBe(false);
    expect(prepared.uploadName).toBe('example.py');
    expect(prepared.input).toBe(filePath);
  });

  it('aliases unsupported text extensions without changing the source file', () => {
    const filePath = createFile('example.ts', 'export const value = 1;\n');
    const prepared = prepareCodeUpload(filePath);

    expect(prepared.aliased).toBe(true);
    expect(prepared.originalName).toBe('example.ts');
    expect(prepared.uploadName).toBe('example.ts.txt');
    expect(typeof prepared.input).toBe('object');
    if (typeof prepared.input !== 'string') {
      expect(prepared.input.mimeType).toBe('text/plain');
      expect(prepared.input.buffer.toString()).toContain('export const value');
    }
  });

  it('rejects empty files', () => {
    const filePath = createFile('empty.js', '');
    expect(() => prepareCodeUpload(filePath)).toThrow('Code file is empty');
  });

  it('rejects unsupported binary files', () => {
    const filePath = createFile('binary.bin', Buffer.from([1, 2, 0, 3]));
    expect(() => prepareCodeUpload(filePath)).toThrow('Unsupported binary code file');
  });
});
