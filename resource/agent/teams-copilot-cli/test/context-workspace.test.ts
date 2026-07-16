import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { buildRepoMap, expandFileReferences, findProjectRoot } from '../src/context/workspace.js';
import { loadMemory } from '../src/context/memory.js';

let root: string;

beforeEach(() => {
  root = mkdtempSync(join(tmpdir(), 'tcc-ws-'));
});

afterEach(() => {
  rmSync(root, { recursive: true, force: true });
});

describe('findProjectRoot', () => {
  it('walks up to the directory containing .git', () => {
    mkdirSync(join(root, '.git'));
    mkdirSync(join(root, 'src', 'deep'), { recursive: true });
    expect(findProjectRoot(join(root, 'src', 'deep'))).toBe(root);
  });

  it('falls back to the start dir when no .git exists', () => {
    mkdirSync(join(root, 'plain'));
    expect(findProjectRoot(join(root, 'plain'))).toBe(join(root, 'plain'));
  });
});

describe('buildRepoMap', () => {
  it('renders an indented tree and skips ignored dirs', () => {
    mkdirSync(join(root, 'src'), { recursive: true });
    mkdirSync(join(root, 'node_modules', 'x'), { recursive: true });
    writeFileSync(join(root, 'src', 'app.ts'), '');
    writeFileSync(join(root, 'readme.md'), '');
    writeFileSync(join(root, 'node_modules', 'x', 'i.js'), '');
    const map = buildRepoMap(root);
    expect(map).toContain('src/');
    expect(map).toContain('  app.ts');
    expect(map).toContain('readme.md');
    expect(map).not.toContain('node_modules');
  });
});

describe('loadMemory', () => {
  it('loads AGENTS.md and truncates long content', () => {
    expect(loadMemory(root)).toBeUndefined();
    writeFileSync(join(root, 'AGENTS.md'), 'x'.repeat(9000));
    expect(loadMemory(root)).toContain('已截断');
    writeFileSync(join(root, 'AGENTS.md'), '规则一');
    expect(loadMemory(root)).toBe('规则一');
  });
});

describe('expandFileReferences', () => {
  it('appends referenced file contents and ignores missing/escaping paths', () => {
    writeFileSync(join(root, 'notes.md'), 'file body');
    const expanded = expandFileReferences('看看 @notes.md 和 @missing.md', root);
    expect(expanded).toContain('file body');
    expect(expanded).toContain('notes.md 的内容');
    expect(expanded).not.toContain('missing.md 的内容');
    expect(expandFileReferences('无引用', root)).toBe('无引用');
  });
});
