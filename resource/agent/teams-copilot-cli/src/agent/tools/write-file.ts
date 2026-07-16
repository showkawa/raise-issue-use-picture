import { mkdirSync, writeFileSync } from 'fs';
import { dirname } from 'path';
import type { Tool, ToolContext, ToolResult } from './types.js';
import { resolveInsideRoot } from './fs-utils.js';

export interface WriteFileArgs {
  path: string;
  content: string;
}

export const writeFileTool: Tool<WriteFileArgs> = {
  name: 'write_file',
  description: '整文件写入（覆盖已有内容）；父目录不存在时自动创建',
  risk: 'write',
  schema: {
    type: 'object',
    properties: {
      path: { type: 'string' },
      content: { type: 'string' },
    },
    required: ['path', 'content'],
  },
  async run(args: WriteFileArgs, ctx: ToolContext): Promise<ToolResult> {
    let absolute: string;
    try {
      absolute = resolveInsideRoot(ctx.projectRoot, args.path);
    } catch (error) {
      return { ok: false, output: error instanceof Error ? error.message : String(error) };
    }
    try {
      mkdirSync(dirname(absolute), { recursive: true });
      writeFileSync(absolute, args.content, 'utf8');
    } catch (error) {
      return { ok: false, output: `Cannot write ${args.path}: ${error instanceof Error ? error.message : String(error)}` };
    }
    return { ok: true, output: `Wrote ${args.content.length} characters to ${args.path}` };
  },
};
