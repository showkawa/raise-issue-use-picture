import { existsSync, readFileSync } from 'fs';
import { dirname, join, resolve } from 'path';
import { walkFiles } from '../agent/tools/fs-utils.js';
import type { WorkspaceInfo } from '../agent/system-prompt.js';
import { isSensitivePath, redactSecrets } from '../agent/redaction.js';
import { loadMemory } from './memory.js';

const REPO_MAP_MAX_ENTRIES = 200;

/** Walks up from cwd looking for a .git directory; falls back to cwd. */
export function findProjectRoot(startDir: string = process.cwd()): string {
  let current = resolve(startDir);
  for (;;) {
    if (existsSync(join(current, '.git'))) return current;
    const parent = dirname(current);
    if (parent === current) return resolve(startDir);
    current = parent;
  }
}

/** Builds a compact indented tree of the repo (gitignore-aware, capped). */
export function buildRepoMap(projectRoot: string, maxEntries = REPO_MAP_MAX_ENTRIES): string {
  const files = walkFiles(projectRoot, { maxEntries: maxEntries * 4 });
  const dirs = new Set<string>();
  const lines: string[] = [];
  let count = 0;
  for (const file of files) {
    if (count >= maxEntries) break;
    const parts = file.split('/');
    for (let depth = 0; depth < parts.length - 1; depth++) {
      const dir = parts.slice(0, depth + 1).join('/');
      if (!dirs.has(dir)) {
        dirs.add(dir);
        lines.push(`${'  '.repeat(depth)}${parts[depth]}/`);
        count += 1;
        if (count >= maxEntries) break;
      }
    }
    if (count >= maxEntries) break;
    lines.push(`${'  '.repeat(parts.length - 1)}${parts[parts.length - 1]}`);
    count += 1;
  }
  const truncated = files.length > count;
  return lines.join('\n') + (truncated ? `\n...(仅显示前 ${count} 项)` : '');
}

export function buildWorkspaceInfo(startDir?: string): WorkspaceInfo {
  const projectRoot = findProjectRoot(startDir);
  return {
    projectRoot,
    repoMap: buildRepoMap(projectRoot),
    memory: loadMemory(projectRoot),
    os: process.platform === 'win32' ? 'Windows' : process.platform,
    shell: process.platform === 'win32' ? 'PowerShell' : 'sh',
  };
}

const FILE_REFERENCE = /@([\w./\\-]+)/g;
const FILE_REFERENCE_BUDGET = 6000;

/** Expands @path/to/file references in user input by appending file contents. */
export function expandFileReferences(input: string, projectRoot: string): string {
  const attachments: string[] = [];
  let budget = FILE_REFERENCE_BUDGET;
  for (const match of input.matchAll(FILE_REFERENCE)) {
    const relPath = match[1];
    const absolute = resolve(projectRoot, relPath);
    if (!absolute.startsWith(resolve(projectRoot)) || !existsSync(absolute)) continue;
    if (isSensitivePath(relPath)) {
      attachments.push(`\n\n文件 ${relPath}：已跳过（敏感文件，不发送给 Copilot）。`);
      continue;
    }
    try {
      let content = redactSecrets(readFileSync(absolute, 'utf8'));
      if (content.length > budget) {
        content = `${content.slice(0, budget)}\n...[内容过长已截断]`;
      }
      budget = Math.max(0, budget - content.length);
      attachments.push(`\n\n文件 ${relPath} 的内容：\n\`\`\`\n${content}\n\`\`\``);
    } catch {
      continue;
    }
  }
  return attachments.length > 0 ? `${input}${attachments.join('')}` : input;
}
