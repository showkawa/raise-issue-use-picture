import { existsSync, readFileSync, readdirSync, statSync } from 'fs';
import { isAbsolute, join, relative, resolve, sep } from 'path';

/** Resolves a path and guarantees it stays inside the project root. */
export function resolveInsideRoot(projectRoot: string, inputPath: string): string {
  const absolute = isAbsolute(inputPath) ? resolve(inputPath) : resolve(projectRoot, inputPath);
  const rel = relative(resolve(projectRoot), absolute);
  if (rel === '') return absolute;
  if (rel.startsWith('..') || isAbsolute(rel)) {
    throw new Error(`Path escapes the project root: ${inputPath}`);
  }
  return absolute;
}

const ALWAYS_IGNORED = new Set(['.git', 'node_modules', 'dist', 'out', 'coverage', '.next', 'build']);

export interface WalkOptions {
  maxEntries?: number;
  maxDepth?: number;
}

interface IgnoreRule {
  regex: RegExp;
  dirOnly: boolean;
}

function gitignoreRules(projectRoot: string): IgnoreRule[] {
  const gitignorePath = join(projectRoot, '.gitignore');
  if (!existsSync(gitignorePath)) return [];
  const rules: IgnoreRule[] = [];
  for (const rawLine of readFileSync(gitignorePath, 'utf8').split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#') || line.startsWith('!')) continue;
    const dirOnly = line.endsWith('/');
    let pattern = dirOnly ? line.slice(0, -1) : line;
    const anchored = pattern.startsWith('/');
    if (anchored) pattern = pattern.slice(1);
    const regexBody = pattern
      .replace(/[.+^${}()|[\]\\]/g, '\\$&')
      .replace(/\*\*/g, '\u0000')
      .replace(/\*/g, '[^/]*')
      .replace(/\?/g, '[^/]')
      .replace(/\u0000/g, '.*');
    const prefix = anchored ? '^' : '(^|/)';
    rules.push({ regex: new RegExp(`${prefix}${regexBody}(/|$)`), dirOnly });
  }
  return rules;
}

function isIgnored(relPath: string, isDir: boolean, rules: IgnoreRule[]): boolean {
  const normalized = relPath.split(sep).join('/');
  const base = normalized.split('/').pop() ?? normalized;
  if (ALWAYS_IGNORED.has(base)) return true;
  return rules.some((rule) => (!rule.dirOnly || isDir) && rule.regex.test(normalized));
}

/** Walks the project tree (gitignore-aware), returning relative file paths with '/' separators. */
export function walkFiles(projectRoot: string, options: WalkOptions = {}): string[] {
  const maxEntries = options.maxEntries ?? 5000;
  const maxDepth = options.maxDepth ?? 12;
  const rules = gitignoreRules(projectRoot);
  const files: string[] = [];
  const queue: Array<{ dir: string; depth: number }> = [{ dir: projectRoot, depth: 0 }];
  while (queue.length > 0 && files.length < maxEntries) {
    const { dir, depth } = queue.shift()!;
    let entries: string[];
    try {
      entries = readdirSync(dir);
    } catch {
      continue;
    }
    for (const name of entries.sort()) {
      if (files.length >= maxEntries) break;
      const full = join(dir, name);
      const rel = relative(projectRoot, full);
      let stats;
      try {
        stats = statSync(full);
      } catch {
        continue;
      }
      if (stats.isDirectory()) {
        if (depth < maxDepth && !isIgnored(rel, true, rules)) {
          queue.push({ dir: full, depth: depth + 1 });
        }
      } else if (!isIgnored(rel, false, rules)) {
        files.push(rel.split(sep).join('/'));
      }
    }
  }
  return files;
}

/** Converts a glob pattern to a RegExp over '/'-separated relative paths. */
export function globToRegExp(pattern: string): RegExp {
  const normalized = pattern.split('\\').join('/');
  const body = normalized
    .replace(/[.+^${}()|[\]]/g, '\\$&')
    .replace(/\*\*\//g, '\u0001')
    .replace(/\*\*/g, '\u0002')
    .replace(/\*/g, '[^/]*')
    .replace(/\?/g, '[^/]')
    .replace(/\u0001/g, '(?:.*/)?')
    .replace(/\u0002/g, '.*');
  return new RegExp(`^${body}$`);
}
