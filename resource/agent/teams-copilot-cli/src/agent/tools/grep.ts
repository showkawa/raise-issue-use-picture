import { readFileSync, statSync } from 'fs';
import { join } from 'path';
import type { Tool, ToolContext, ToolResult } from './types.js';
import { globToRegExp, walkFiles } from './fs-utils.js';

const MAX_MATCHES = 100;
const MAX_FILE_BYTES = 1024 * 1024;

export interface GrepArgs {
  pattern: string;
  glob?: string;
  path?: string;
}

export const grepTool: Tool<GrepArgs> = {
  name: 'grep',
  description: '在项目内按正则搜索文件内容（.gitignore 感知），返回 文件:行号:内容',
  risk: 'read',
  schema: {
    type: 'object',
    properties: {
      pattern: { type: 'string', description: 'JS 正则' },
      glob: { type: 'string', description: '文件过滤，如 src/**/*.ts' },
      path: { type: 'string', description: '限定子目录' },
    },
    required: ['pattern'],
  },
  async run(args: GrepArgs, ctx: ToolContext): Promise<ToolResult> {
    let regex: RegExp;
    try {
      regex = new RegExp(args.pattern);
    } catch (error) {
      return { ok: false, output: `Invalid regex: ${error instanceof Error ? error.message : String(error)}` };
    }
    const globRegex = args.glob ? globToRegExp(args.glob) : null;
    const prefix = args.path ? args.path.split('\\').join('/').replace(/\/+$/, '') + '/' : '';
    const files = walkFiles(ctx.projectRoot);
    const matches: string[] = [];
    let scanned = 0;
    for (const file of files) {
      if (prefix && !file.startsWith(prefix) && file !== prefix.slice(0, -1)) continue;
      if (globRegex && !globRegex.test(file)) continue;
      const full = join(ctx.projectRoot, file);
      try {
        if (statSync(full).size > MAX_FILE_BYTES) continue;
        const lines = readFileSync(full, 'utf8').split(/\r?\n/);
        scanned += 1;
        for (const [index, line] of lines.entries()) {
          if (regex.test(line)) {
            matches.push(`${file}:${index + 1}:${line.trim().slice(0, 300)}`);
            if (matches.length >= MAX_MATCHES) break;
          }
        }
      } catch {
        continue;
      }
      if (matches.length >= MAX_MATCHES) break;
    }
    const truncatedNote = matches.length >= MAX_MATCHES ? `\n[结果达到 ${MAX_MATCHES} 条上限，已截断]` : '';
    return {
      ok: true,
      output: matches.length > 0
        ? `${matches.join('\n')}${truncatedNote}`
        : `No matches for /${args.pattern}/ (scanned ${scanned} files)`,
    };
  },
};
