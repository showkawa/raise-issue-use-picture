/**
 * cli/index.ts — 统一 CLI 入口
 * 用法: bun run cli/index.ts <command> [args...]
 *
 * 支持的命令:
 *   ask      <question>      自由问答
 *   prd      <project-name>  生成 PRD
 *   arch     <project-name>  生成架构设计（需先运行 prd）
 *   tasks    <project-name>  生成任务拆解（需先运行 prd 和 arch）
 */

import { askCommand } from './ask.js';
import { prdCommand } from './prd.js';
import { archCommand } from './arch.js';
import { tasksCommand } from './tasks.js';

const args = process.argv.slice(2);
const command = args[0];

const usage = `Teams Copilot CLI — GPT-5.5 Planning Runtime

用法: bun run cli/index.ts <command> [args...]

命令:
  ask      <question>       自由问答 Teams Copilot
  prd      <project-name>   为指定项目生成产品需求文档
  arch     <project-name>   为指定项目生成架构设计文档（依赖 PRD）
  tasks    <project-name>   为指定项目生成任务拆解文档（依赖 PRD + 架构）

示例:
  bun run cli/index.ts ask "如何做数据库分库分表？"
  bun run cli/index.ts prd my-app
  bun run cli/index.ts arch my-app
  bun run cli/index.ts tasks my-app

选项:
  --config <path>           指定 config.yaml 路径
  --prd <path>              指定 PRD 文件路径（arch/tasks 命令）
  --arch <path>             指定架构文件路径（tasks 命令）
`;

if (!command) {
  console.log(usage);
  process.exit(0);
}

// 提取全局 --config 选项
const configIndex = args.indexOf('--config');
const configPath = configIndex !== -1 ? args[configIndex + 1] : undefined;

switch (command) {
  case 'ask':
    askCommand(args[1]);
    break;
  case 'prd':
    prdCommand(args[1], configPath);
    break;
  case 'arch': {
    const prdIdx = args.indexOf('--prd');
    archCommand(args[1], prdIdx !== -1 ? args[prdIdx + 1] : undefined, configPath);
    break;
  }
  case 'tasks': {
    const prdIdx = args.indexOf('--prd');
    const archIdx = args.indexOf('--arch');
    tasksCommand(
      args[1],
      prdIdx !== -1 ? args[prdIdx + 1] : undefined,
      archIdx !== -1 ? args[archIdx + 1] : undefined,
      configPath,
    );
    break;
  }
  default:
    console.error(`未知命令: ${command}`);
    console.log(usage);
    process.exit(1);
}
