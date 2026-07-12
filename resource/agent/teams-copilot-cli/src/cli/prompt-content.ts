import { dirname, extname, resolve } from 'path';
import { mkdirSync, readFileSync, statSync, writeFileSync } from 'fs';
import { createInterface } from 'readline';
import type { Stats } from 'fs';

const LANGUAGES: Record<string, string> = {
  '.bash': 'bash',
  '.c': 'c',
  '.cpp': 'cpp',
  '.cs': 'csharp',
  '.css': 'css',
  '.dart': 'dart',
  '.go': 'go',
  '.h': 'c',
  '.htm': 'html',
  '.html': 'html',
  '.java': 'java',
  '.js': 'javascript',
  '.json': 'json',
  '.jsx': 'jsx',
  '.kt': 'kotlin',
  '.kts': 'kotlin',
  '.lua': 'lua',
  '.md': 'markdown',
  '.php': 'php',
  '.pl': 'perl',
  '.py': 'python',
  '.rb': 'ruby',
  '.rs': 'rust',
  '.scala': 'scala',
  '.sh': 'bash',
  '.sql': 'sql',
  '.swift': 'swift',
  '.toml': 'toml',
  '.ts': 'typescript',
  '.tsx': 'tsx',
  '.vue': 'vue',
  '.xml': 'xml',
  '.yaml': 'yaml',
  '.yml': 'yaml',
};

export function inferCodeLanguage(filePath: string): string {
  return LANGUAGES[extname(filePath).toLowerCase()] ?? 'text';
}

export function buildCodePrompt(
  question: string,
  content: string,
  language = 'text',
): string {
  if (!content) throw new Error('Text content is empty');
  if (content.includes('\0')) throw new Error('Text content contains binary data');

  const longestBacktickRun = Math.max(
    0,
    ...(content.match(/`+/g) ?? []).map((run) => run.length),
  );
  const fence = '`'.repeat(Math.max(3, longestBacktickRun + 1));
  const safeLanguage = language.replace(/[^a-z0-9_+#.-]/gi, '') || 'text';
  const trailingNewline = content.endsWith('\n') ? '' : '\n';
  return `${question}\n\n${fence}${safeLanguage}\n${content}${trailingNewline}${fence}`;
}

export function readTextFile(filePath: string): string {
  const absolutePath = resolve(filePath);
  let stats: Stats;
  try {
    stats = statSync(absolutePath);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') {
      throw new Error(`Text file not found: ${filePath}`);
    }
    throw error;
  }
  if (!stats.isFile()) throw new Error(`Not a file: ${filePath}`);
  return readFileSync(absolutePath, 'utf8');
}

export async function readStandardInput(required = true): Promise<string> {
  if (process.stdin.isTTY) {
    if (!required) return '';
    throw new Error('No stdin content received; pipe text into tcc or use --file');
  }
  process.stdin.setEncoding('utf8');
  let content = '';
  for await (const chunk of process.stdin) content += chunk;
  if (required && !content) {
    throw new Error('No stdin content received; pipe text into tcc or use --file');
  }
  return content;
}

export async function readDelimitedQuestion(
  input: NodeJS.ReadableStream = process.stdin,
  terminator = '@',
): Promise<string> {
  if (input === process.stdin && process.stdin.isTTY) {
    process.stderr.write(`Enter the multiline prompt; finish with ${terminator} on its own line.\n`);
  }

  const lines: string[] = [];
  let terminated = false;
  const reader = createInterface({ input, crlfDelay: Infinity, terminal: false });
  for await (const line of reader) {
    if (line === terminator) {
      terminated = true;
      break;
    }
    lines.push(line);
  }
  reader.close();

  if (!terminated) {
    throw new Error(`Multiline prompt must end with ${terminator} on its own line`);
  }
  if (lines.length === 0) {
    throw new Error('Multiline prompt is empty');
  }
  return lines.join('\n');
}

export function writeTextOutput(outputPath: string, content: string): string {
  const resolvedPath = resolve(outputPath);
  mkdirSync(dirname(resolvedPath), { recursive: true });
  writeFileSync(resolvedPath, content, 'utf8');
  return resolvedPath;
}
