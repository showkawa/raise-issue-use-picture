import { readFileSync, writeFileSync } from 'fs';
import type { Tool, ToolContext, ToolResult } from './types.js';
import { resolveInsideRoot } from './fs-utils.js';

export interface EditFileArgs {
  path: string;
  old: string;
  new: string;
  all?: boolean;
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

/** Returns a snippet of the file around the closest partial match, to ground corrections. */
function nearbyContext(content: string, old: string): string {
  const collapse = (text: string) => text.replace(/\s+/g, ' ').trim();
  const probe = old.split(/\r?\n/).map(collapse).find((line) => line.length > 8);
  if (!probe) return '';
  const lines = content.split(/\r?\n/);
  const hit = lines.findIndex((line) => collapse(line).includes(probe) || probe.includes(collapse(line)) && collapse(line).length > 8);
  if (hit === -1) return '';
  const start = Math.max(0, hit - 3);
  const context = lines.slice(start, hit + 4)
    .map((line, index) => `${start + index + 1}| ${line}`)
    .join('\n');
  return `\n磁盘上的邻近内容（以此为准重新构造 old）：\n${context}`;
}

export const editFileTool: Tool<EditFileArgs> = {
  name: 'edit_file',
  description: '精确字符串替换；old 必须与磁盘内容完全一致且唯一（all=true 时替换全部出现）',
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
    if (occurrences === 0) {
      return {
        ok: false,
        output: `old 在 ${args.path} 中未找到（必须逐字符一致，包含空格与换行）。${nearbyContext(content, args.old)}`,
      };
    }
    if (occurrences > 1 && !args.all) {
      return {
        ok: false,
        output: `old 在 ${args.path} 中出现 ${occurrences} 次，无法确定替换目标。请扩大 old 的上下文使其唯一，或传 all=true 替换全部。`,
      };
    }
    const updated = args.all
      ? content.split(args.old).join(args.new)
      : content.replace(args.old, args.new);
    try {
      writeFileSync(absolute, updated, 'utf8');
    } catch (error) {
      return { ok: false, output: `Cannot write ${args.path}: ${error instanceof Error ? error.message : String(error)}` };
    }
    return { ok: true, output: `Replaced ${args.all ? occurrences : 1} occurrence(s) in ${args.path}` };
  },
};
