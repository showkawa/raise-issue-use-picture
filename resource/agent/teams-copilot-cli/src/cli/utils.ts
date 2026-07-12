import { existsSync, mkdirSync, readFileSync } from 'fs';
import { join } from 'path';
import { fileURLToPath } from 'url';
import type { BrowserConfig } from '../types.js';
import type { CommandOpts } from './ask.js';

export function browserFlagsFromOptions(opts: CommandOpts): Partial<BrowserConfig> {
  const browserFlags: Partial<BrowserConfig> = {};
  if (opts.browser) browserFlags.path = opts.browser;
  if (opts.port !== undefined) {
    const port = Number.parseInt(opts.port, 10);
    if (!Number.isInteger(port) || port < 1 || port > 65535) {
      throw new Error(`Invalid CDP port: ${opts.port}`);
    }
    browserFlags.port = port;
  }
  return browserFlags;
}

export function readPromptTemplate(fileName: string): string {
  const promptPath = fileURLToPath(new URL(`../../prompts/${fileName}`, import.meta.url));
  return readFileSync(promptPath, 'utf8');
}

export function ensureOutputDir(): string {
  const outputDir = join(process.cwd(), 'output');
  if (!existsSync(outputDir)) mkdirSync(outputDir, { recursive: true });
  return outputDir;
}

export function readRequiredFile(path: string, message: string): string {
  if (!existsSync(path)) throw new Error(message);
  return readFileSync(path, 'utf8');
}
