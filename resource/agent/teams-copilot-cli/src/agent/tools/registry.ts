import type { JsonSchemaLite, Tool } from './types.js';
import { readFileTool } from './read-file.js';
import { writeFileTool } from './write-file.js';
import { editFileTool } from './edit-file.js';
import { runCommandTool } from './run-command.js';
import { grepTool } from './grep.js';
import { globTool } from './glob.js';
import { gitTool } from './git.js';

export type AnyTool = Tool<never>;

export class ToolRegistry {
  private tools = new Map<string, Tool<Record<string, unknown>>>();

  register(tool: Tool<never>): void {
    this.tools.set(tool.name, tool as unknown as Tool<Record<string, unknown>>);
  }

  get(name: string): Tool<Record<string, unknown>> | undefined {
    return this.tools.get(name);
  }

  list(): Array<Tool<Record<string, unknown>>> {
    return [...this.tools.values()];
  }

  schemas(): Map<string, JsonSchemaLite> {
    return new Map([...this.tools.entries()].map(([name, tool]) => [name, tool.schema]));
  }
}

export function createDefaultRegistry(): ToolRegistry {
  const registry = new ToolRegistry();
  registry.register(readFileTool as Tool<never>);
  registry.register(writeFileTool as Tool<never>);
  registry.register(editFileTool as Tool<never>);
  registry.register(runCommandTool as Tool<never>);
  registry.register(grepTool as Tool<never>);
  registry.register(globTool as Tool<never>);
  registry.register(gitTool as Tool<never>);
  return registry;
}
