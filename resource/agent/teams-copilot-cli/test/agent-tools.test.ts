import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'fs';
import { tmpdir } from 'os';
import { join } from 'path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { readFileTool } from '../src/agent/tools/read-file.js';
import { writeFileTool } from '../src/agent/tools/write-file.js';
import { editFileTool } from '../src/agent/tools/edit-file.js';
import { runCommandTool } from '../src/agent/tools/run-command.js';
import { grepTool } from '../src/agent/tools/grep.js';
import { globTool } from '../src/agent/tools/glob.js';
import { gitTool } from '../src/agent/tools/git.js';
import { createDefaultRegistry } from '../src/agent/tools/registry.js';
import { globToRegExp, resolveInsideRoot, walkFiles } from '../src/agent/tools/fs-utils.js';
import type { ToolContext } from '../src/agent/tools/types.js';

let root: string;
let ctx: ToolContext;

beforeEach(() => {
  root = mkdtempSync(join(tmpdir(), 'tcc-tools-'));
  ctx = { projectRoot: root };
});

afterEach(() => {
  rmSync(root, { recursive: true, force: true });
});

describe('resolveInsideRoot', () => {
  it('rejects escaping paths', () => {
    expect(() => resolveInsideRoot(root, '../outside.txt')).toThrow('escapes');
    expect(() => resolveInsideRoot(root, 'a/../../outside.txt')).toThrow('escapes');
    expect(resolveInsideRoot(root, 'a/b.txt')).toContain(root);
  });
});

describe('read_file / write_file / edit_file', () => {
  it('writes, reads with line numbers, and paginates', async () => {
    const write = await writeFileTool.run({ path: 'sub/a.txt', content: 'one\ntwo\nthree' }, ctx);
    expect(write.ok).toBe(true);
    const read = await readFileTool.run({ path: 'sub/a.txt' }, ctx);
    expect(read.ok).toBe(true);
    expect(read.output).toContain('1| one');
    const page = await readFileTool.run({ path: 'sub/a.txt', offset: 2, limit: 1 }, ctx);
    expect(page.output).toContain('2| two');
    expect(page.output).toContain('还有 1 行');
  });

  it('read_file fails outside root or on missing file', async () => {
    expect((await readFileTool.run({ path: '../x' }, ctx)).ok).toBe(false);
    expect((await readFileTool.run({ path: 'missing.txt' }, ctx)).ok).toBe(false);
  });

  it('read_file refuses sensitive files', async () => {
    writeFileSync(join(root, '.env'), 'API_TOKEN=supersecretvalue\n');
    const result = await readFileTool.run({ path: '.env' }, ctx);
    expect(result.ok).toBe(false);
    expect(result.output).toContain('拒绝读取敏感文件');
  });

  it('edit_file replaces exact unique matches', async () => {
    writeFileSync(join(root, 'a.ts'), 'const x = 1;\nconst y = 2;\n');
    const result = await editFileTool.run({ path: 'a.ts', old: 'const x = 1;', new: 'const x = 9;' }, ctx);
    expect(result.ok).toBe(true);
    expect(readFileSync(join(root, 'a.ts'), 'utf8')).toContain('const x = 9;');
  });

  it('edit_file falls back to whitespace-normalized matching and replaces disk original', async () => {
    // Disk has tab indentation and CRLF; model supplies spaces + LF + drifted internal spacing.
    writeFileSync(join(root, 'a.ts'), 'function greet() {\r\n\treturn "hello world";\r\n}\r\n');
    const result = await editFileTool.run(
      { path: 'a.ts', old: '    return "hello  world";', new: '\treturn "hi";' },
      ctx,
    );
    expect(result.ok).toBe(true);
    const after = readFileSync(join(root, 'a.ts'), 'utf8');
    expect(after).toBe('function greet() {\r\n\treturn "hi";\r\n}\r\n');
  });

  it('edit_file reports genuine misses with nearby disk context', async () => {
    writeFileSync(join(root, 'a.ts'), 'function greet() {\n  return "hello world";\n}\n');
    const result = await editFileTool.run(
      { path: 'a.ts', old: '  return "hello world"; // a trailing comment not on disk', new: 'x' },
      ctx,
    );
    expect(result.ok).toBe(false);
    expect(result.output).toContain('未找到');
    expect(result.output).toContain('return "hello world"');
  });

  it('edit_file refuses a non-unique normalized match unless all=true', async () => {
    // Internal double spaces on disk mean the exact single-space `old` is absent,
    // so the normalized cascade runs and finds two candidates.
    writeFileSync(join(root, 'a.ts'), '  value  =  1;\n\tvalue  =  1;\n');
    const ambiguous = await editFileTool.run({ path: 'a.ts', old: 'value = 1;', new: 'value = 2;' }, ctx);
    expect(ambiguous.ok).toBe(false);
    expect(ambiguous.output).toContain('无法确定');
    const all = await editFileTool.run({ path: 'a.ts', old: 'value = 1;', new: 'value = 2;', all: true }, ctx);
    expect(all.ok).toBe(true);
    expect(readFileSync(join(root, 'a.ts'), 'utf8')).toBe('value = 2;\nvalue = 2;\n');
  });

  it('edit_file rejects ambiguous exact old unless all=true', async () => {
    writeFileSync(join(root, 'a.ts'), 'dup\ndup\n');
    const ambiguous = await editFileTool.run({ path: 'a.ts', old: 'dup', new: 'x' }, ctx);
    expect(ambiguous.ok).toBe(false);
    expect(ambiguous.output).toContain('2 次');
    const all = await editFileTool.run({ path: 'a.ts', old: 'dup', new: 'x', all: true }, ctx);
    expect(all.ok).toBe(true);
    expect(readFileSync(join(root, 'a.ts'), 'utf8')).toBe('x\nx\n');
  });
});

describe('run_command', () => {
  it('captures stdout and exit code', async () => {
    const result = await runCommandTool.run({ command: 'echo hello-agent' }, ctx);
    expect(result.ok).toBe(true);
    expect(result.exitCode).toBe(0);
    expect(result.output).toContain('hello-agent');
  });

  it('reports non-zero exit codes', async () => {
    const result = await runCommandTool.run({ command: 'exit 3' }, ctx);
    expect(result.ok).toBe(false);
    expect(result.exitCode).toBe(3);
  });

  it('kills commands on timeout', async () => {
    const sleep = process.platform === 'win32' ? 'Start-Sleep -Seconds 30' : 'sleep 30';
    const result = await runCommandTool.run({ command: sleep, timeoutMs: 1500 }, ctx);
    expect(result.ok).toBe(false);
    expect(result.output).toContain('超时');
  }, 15000);

  it('rejects cwd outside the project root', async () => {
    const result = await runCommandTool.run({ command: 'echo x', cwd: '..' }, ctx);
    expect(result.ok).toBe(false);
  });
});

describe('grep / glob / walkFiles', () => {
  beforeEach(() => {
    mkdirSync(join(root, 'src'), { recursive: true });
    mkdirSync(join(root, 'node_modules', 'pkg'), { recursive: true });
    writeFileSync(join(root, 'src', 'app.ts'), 'const needle = 42;\n');
    writeFileSync(join(root, 'src', 'other.js'), 'nothing here\n');
    writeFileSync(join(root, 'node_modules', 'pkg', 'index.js'), 'const needle = 0;\n');
    writeFileSync(join(root, 'ignored.log'), 'const needle = 1;\n');
    writeFileSync(join(root, '.gitignore'), '*.log\n');
  });

  it('walkFiles honors .gitignore and built-in ignores', () => {
    const files = walkFiles(root);
    expect(files).toContain('src/app.ts');
    expect(files.some((file) => file.startsWith('node_modules'))).toBe(false);
    expect(files).not.toContain('ignored.log');
  });

  it('grep finds matches with file:line output', async () => {
    const result = await grepTool.run({ pattern: 'needle = \\d+' }, ctx);
    expect(result.ok).toBe(true);
    expect(result.output).toContain('src/app.ts:1:');
    expect(result.output).not.toContain('node_modules');
  });

  it('grep supports glob and path filters', async () => {
    const byGlob = await grepTool.run({ pattern: 'needle', glob: '**/*.js' }, ctx);
    expect(byGlob.output).toContain('No matches');
    const byPath = await grepTool.run({ pattern: 'needle', path: 'src' }, ctx);
    expect(byPath.output).toContain('src/app.ts');
  });

  it('glob matches patterns', async () => {
    const result = await globTool.run({ pattern: 'src/**/*.ts' }, ctx);
    expect(result.output).toContain('src/app.ts');
    expect(result.output).not.toContain('other.js');
    expect(globToRegExp('src/*.ts').test('src/a/b.ts')).toBe(false);
    expect(globToRegExp('src/**/*.ts').test('src/a/b.ts')).toBe(true);
  });
});

describe('git tool', () => {
  it('rejects unsupported subcommands, forbidden flags and blank commits', async () => {
    expect((await gitTool.run({ subcommand: 'push' as never }, ctx)).ok).toBe(false);
    expect((await gitTool.run({ subcommand: 'add', args: ['-A'] }, ctx)).ok).toBe(false);
    expect((await gitTool.run({ subcommand: 'add', args: [] }, ctx)).ok).toBe(false);
    expect((await gitTool.run({ subcommand: 'commit' }, ctx)).ok).toBe(false);
  });

  it('runs status/add/commit in a real repo', async () => {
    const git = async (args: string[]) => {
      const { execFileSync } = await import('child_process');
      execFileSync('git', args, { cwd: root });
    };
    await git(['init', '-q']);
    await git(['config', 'user.email', 'test@example.com']);
    await git(['config', 'user.name', 'Test']);
    writeFileSync(join(root, 'f.txt'), 'x');
    const status = await gitTool.run({ subcommand: 'status' }, ctx);
    expect(status.ok).toBe(true);
    expect(status.output).toContain('f.txt');
    expect((await gitTool.run({ subcommand: 'add', args: ['f.txt'] }, ctx)).ok).toBe(true);
    const commit = await gitTool.run({ subcommand: 'commit', message: 'test: add f' }, ctx);
    expect(commit.ok).toBe(true);
    const log = await gitTool.run({ subcommand: 'log' }, ctx);
    expect(log.output).toContain('test: add f');
  });
});

describe('registry', () => {
  it('registers the seven P0 tools with schemas', () => {
    const registry = createDefaultRegistry();
    const names = registry.list().map((tool) => tool.name).sort();
    expect(names).toEqual(['edit_file', 'git', 'glob', 'grep', 'read_file', 'run_command', 'write_file']);
    expect(registry.schemas().get('edit_file')?.required).toContain('old');
    expect(registry.get('read_file')?.risk).toBe('read');
  });
});
