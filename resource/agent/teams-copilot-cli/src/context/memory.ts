import { existsSync, readFileSync } from 'fs';
import { join } from 'path';

const MEMORY_FILES = ['AGENTS.md', 'TEAMS-COPILOT.md'];
const MEMORY_MAX_CHARS = 8000;

/** Loads project conventions from AGENTS.md / TEAMS-COPILOT.md when present. */
export function loadMemory(projectRoot: string): string | undefined {
  for (const name of MEMORY_FILES) {
    const path = join(projectRoot, name);
    if (existsSync(path)) {
      const content = readFileSync(path, 'utf8').trim();
      if (!content) continue;
      return content.length > MEMORY_MAX_CHARS
        ? `${content.slice(0, MEMORY_MAX_CHARS)}\n...[内容过长已截断]`
        : content;
    }
  }
  return undefined;
}
