import type { Tool, ToolContext, ToolResult } from './types.js';
import { globToRegExp, walkFiles } from './fs-utils.js';

const MAX_RESULTS = 200;

export interface GlobArgs {
  pattern: string;
}

export const globTool: Tool<GlobArgs> = {
  name: 'glob',
  description: '按 glob 模式查找文件（如 src/**/*.ts），.gitignore 感知',
  risk: 'read',
  schema: {
    type: 'object',
    properties: {
      pattern: { type: 'string' },
    },
    required: ['pattern'],
  },
  async run(args: GlobArgs, ctx: ToolContext): Promise<ToolResult> {
    let regex: RegExp;
    try {
      regex = globToRegExp(args.pattern);
    } catch (error) {
      return { ok: false, output: `Invalid glob: ${error instanceof Error ? error.message : String(error)}` };
    }
    const matches = walkFiles(ctx.projectRoot).filter((file) => regex.test(file));
    const shown = matches.slice(0, MAX_RESULTS);
    const truncatedNote = matches.length > MAX_RESULTS ? `\n[共 ${matches.length} 个匹配，仅显示前 ${MAX_RESULTS} 个]` : '';
    return {
      ok: true,
      output: shown.length > 0 ? `${shown.join('\n')}${truncatedNote}` : `No files match ${args.pattern}`,
    };
  },
};
