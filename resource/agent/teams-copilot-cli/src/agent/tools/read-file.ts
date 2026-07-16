import { readFileSync } from 'fs';
import type { Tool, ToolContext, ToolResult } from './types.js';
import { resolveInsideRoot } from './fs-utils.js';

const DEFAULT_LIMIT = 400;

export interface ReadFileArgs {
  path: string;
  offset?: number;
  limit?: number;
}

export const readFileTool: Tool<ReadFileArgs> = {
  name: 'read_file',
  description: '读取文件内容（带行号）；大文件默认只返回前 400 行，可用 offset/limit 分页',
  risk: 'read',
  schema: {
    type: 'object',
    properties: {
      path: { type: 'string', description: '相对项目根的文件路径' },
      offset: { type: 'number', description: '起始行号（1-based）' },
      limit: { type: 'number', description: '返回的最大行数' },
    },
    required: ['path'],
  },
  async run(args: ReadFileArgs, ctx: ToolContext): Promise<ToolResult> {
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
    const lines = content.split(/\r?\n/);
    const offset = Math.max(1, args.offset ?? 1);
    const limit = Math.max(1, args.limit ?? DEFAULT_LIMIT);
    const slice = lines.slice(offset - 1, offset - 1 + limit);
    const numbered = slice.map((line, index) => `${offset + index}| ${line}`).join('\n');
    const remaining = lines.length - (offset - 1 + slice.length);
    const suffix = remaining > 0 ? `\n...[还有 ${remaining} 行未显示，用 offset=${offset + slice.length} 继续读取]` : '';
    return { ok: true, output: `${args.path} (共 ${lines.length} 行)\n${numbered}${suffix}` };
  },
};
