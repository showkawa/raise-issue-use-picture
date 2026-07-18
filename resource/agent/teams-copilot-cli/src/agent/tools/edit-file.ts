import { readFileSync, writeFileSync } from 'fs';
import type { Tool, ToolContext, ToolResult } from './types.js';
import { resolveInsideRoot } from './fs-utils.js';

export interface EditFileArgs {
  path: string;
  old: string;
  new: string;
  all?: boolean;
}

interface IndexedLine {
  text: string;
  start: number;
  textEnd: number;
}

function countOccurrences(haystack: string, needle: string): number {
  if (!needle) return 0;
  let count = 0;
  let index = 0;
  while ((index = haystack.indexOf(needle, index)) !== -1) {
    count += 1;
    index += needle.length;
  }
  return count;
}

/** Splits content into lines, recording each line's char offsets (EOL excluded from textEnd). */
function indexLines(content: string): IndexedLine[] {
  const result: IndexedLine[] = [];
  const re = /\r?\n/g;
  let lineStart = 0;
  let match: RegExpExecArray | null;
  while ((match = re.exec(content)) !== null) {
    result.push({ text: content.slice(lineStart, match.index), start: lineStart, textEnd: match.index });
    lineStart = match.index + match[0].length;
  }
  result.push({ text: content.slice(lineStart), start: lineStart, textEnd: content.length });
  return result;
}

/** Splits `old` into lines, dropping a single trailing empty line (a trailing newline in old). */
function oldLines(old: string): string[] {
  const lines = old.split(/\r?\n/);
  if (lines.length > 1 && lines[lines.length - 1] === '') lines.pop();
  return lines;
}

const trimEnds = (line: string): string => line.replace(/^[ \t\f\v]+/, '').replace(/[ \t\f\v]+$/, '');
const trimCollapse = (line: string): string => line.trim().replace(/\s+/g, ' ');

/** Finds line-window matches of `needle` in `lines` under a per-line normalizer. */
function findWindows(
  lines: IndexedLine[],
  needle: string[],
  normalize: (line: string) => string,
): number[] {
  const n = needle.length;
  if (n === 0 || n > lines.length) return [];
  const normNeedle = needle.map(normalize);
  const starts: number[] = [];
  for (let i = 0; i + n <= lines.length; i++) {
    let ok = true;
    for (let j = 0; j < n; j++) {
      if (normalize(lines[i + j].text) !== normNeedle[j]) { ok = false; break; }
    }
    if (ok) starts.push(i);
  }
  return starts;
}

/** Returns a snippet of the file around the closest partial match, to ground corrections. */
function nearbyContext(content: string, old: string): string {
  const collapse = (text: string) => text.replace(/\s+/g, ' ').trim();
  const probe = old.split(/\r?\n/).map(collapse).find((line) => line.length > 8);
  if (!probe) return '';
  const lines = content.split(/\r?\n/);
  const hit = lines.findIndex((line) => collapse(line).includes(probe) || (probe.includes(collapse(line)) && collapse(line).length > 8));
  if (hit === -1) return '';
  const start = Math.max(0, hit - 3);
  const context = lines.slice(start, hit + 4)
    .map((line, index) => `${start + index + 1}| ${line}`)
    .join('\n');
  return `\n磁盘上的邻近内容（以此为准重新构造 old）：\n${context}`;
}

/** Replaces the disk-original text of the given line windows with `replacement`. */
function replaceWindows(content: string, lines: IndexedLine[], starts: number[], windowLen: number, replacement: string): string {
  let updated = content;
  // Replace from last to first so earlier offsets stay valid.
  for (const start of [...starts].sort((a, b) => b - a)) {
    const from = lines[start].start;
    const to = lines[start + windowLen - 1].textEnd;
    updated = updated.slice(0, from) + replacement + updated.slice(to);
  }
  return updated;
}

interface CascadeResult {
  updated?: string;
  replaced: number;
  ambiguous?: number;
  tier?: string;
}

/** Tries whitespace-normalized line matching tiers after an exact match fails. */
function normalizedCascade(content: string, args: EditFileArgs): CascadeResult {
  const lines = indexLines(content);
  const needle = oldLines(args.old);
  for (const [tier, normalize] of [['行首尾空白归一', trimEnds], ['逐行 trim 归一', trimCollapse]] as const) {
    const starts = findWindows(lines, needle, normalize);
    if (starts.length === 0) continue;
    if (starts.length > 1 && !args.all) {
      return { replaced: 0, ambiguous: starts.length, tier };
    }
    const updated = replaceWindows(content, lines, starts, needle.length, args.new);
    return { updated, replaced: starts.length, tier };
  }
  return { replaced: 0 };
}

export const editFileTool: Tool<EditFileArgs> = {
  name: 'edit_file',
  description: '字符串替换；old 优先逐字符匹配，失败时按空白归一在磁盘原文上替换（all=true 时替换全部出现）',
  risk: 'write',
  schema: {
    type: 'object',
    properties: {
      path: { type: 'string' },
      old: { type: 'string' },
      new: { type: 'string' },
      all: { type: 'boolean' },
    },
    required: ['path', 'old', 'new'],
  },
  async run(args: EditFileArgs, ctx: ToolContext): Promise<ToolResult> {
    let absolute: string;
    try {
      absolute = resolveInsideRoot(ctx.projectRoot, args.path);
    } catch (error) {
      return { ok: false, output: error instanceof Error ? error.message : String(error) };
    }
    let content: string;
    try {
      content = readFileSync(absolute, 'utf8');
    } catch (error) {
      return { ok: false, output: `Cannot read ${args.path}: ${error instanceof Error ? error.message : String(error)}` };
    }
    if (!args.old) {
      return { ok: false, output: 'old must not be empty' };
    }

    const occurrences = countOccurrences(content, args.old);
    if (occurrences > 1 && !args.all) {
      return {
        ok: false,
        output: `old 在 ${args.path} 中出现 ${occurrences} 次，无法确定替换目标。请扩大 old 的上下文使其唯一，或传 all=true 替换全部。`,
      };
    }

    let updated: string;
    let replaced: number;
    let note = '';
    if (occurrences >= 1) {
      updated = args.all ? content.split(args.old).join(args.new) : content.replace(args.old, args.new);
      replaced = args.all ? occurrences : 1;
    } else {
      const cascade = normalizedCascade(content, args);
      if (cascade.ambiguous) {
        return {
          ok: false,
          output: `old 在 ${args.path} 中逐字符未找到；按${cascade.tier}后匹配到 ${cascade.ambiguous} 处，无法确定替换目标。请扩大 old 的上下文使其唯一，或传 all=true。`,
        };
      }
      if (cascade.updated === undefined) {
        return {
          ok: false,
          output: `old 在 ${args.path} 中未找到（逐字符与空白归一匹配均失败）。${nearbyContext(content, args.old)}`,
        };
      }
      updated = cascade.updated;
      replaced = cascade.replaced;
      note = `（经${cascade.tier}匹配磁盘原文）`;
    }

    try {
      writeFileSync(absolute, updated, 'utf8');
    } catch (error) {
      return { ok: false, output: `Cannot write ${args.path}: ${error instanceof Error ? error.message : String(error)}` };
    }
    return { ok: true, output: `Replaced ${replaced} occurrence(s) in ${args.path}${note}` };
  },
};
