/**
 * Token-level classification of shell commands (ADR-0008). Used to flag destructive
 * operations regardless of where they appear in the string (not substring-only), and
 * to reject interactive programs that would hang a non-interactive agent run.
 */

export interface CommandClassification {
  destructive: boolean;
  reason?: string;
}

export interface InteractiveClassification {
  interactive: boolean;
  reason?: string;
}

const SEGMENT_SPLIT = /(?:\|\||&&|;|\r?\n|\||&)+/;

/** Splits a command line into segments on shell chaining/piping operators. */
export function splitSegments(command: string): string[] {
  return command.split(SEGMENT_SPLIT).map((part) => part.trim()).filter(Boolean);
}

function tokenize(segment: string): string[] {
  const matches = segment.match(/"[^"]*"|'[^']*'|\S+/g) ?? [];
  return matches.map((token) => token.replace(/^["']|["']$/g, ''));
}

// Constructs that hide arbitrary sub-commands from static inspection: treat as destructive.
const OPAQUE = /\$\(|`|(?:^|[\s;|&(])(?:iex|invoke-expression)\b/i;

function isFlag(rest: string[], ...flags: string[]): boolean {
  return rest.some((token) => flags.includes(token));
}

export function classifyCommand(command: string): CommandClassification {
  if (OPAQUE.test(command)) {
    return {
      destructive: true,
      reason: '包含子命令/表达式求值（$()、反引号或 Invoke-Expression），无法静态判定，按破坏性处理',
    };
  }
  for (const segment of splitSegments(command)) {
    const tokens = tokenize(segment);
    if (tokens.length === 0) continue;
    const head = tokens[0].toLowerCase().replace(/\.exe$/, '');
    const rest = tokens.slice(1).map((token) => token.toLowerCase());

    if ((head === 'rm' || head === 'remove-item' || head === 'ri')
      && isFlag(rest, '-r', '-rf', '-fr', '-f', '-recurse', '-force', '--recursive', '--force')) {
      return { destructive: true, reason: `递归/强制删除（${segment}）` };
    }
    if ((head === 'del' || head === 'erase' || head === 'rd' || head === 'rmdir')
      && isFlag(rest, '/s', '/q')) {
      return { destructive: true, reason: `递归删除（${segment}）` };
    }
    if (head === 'format' || head === 'diskpart' || head === 'mkfs') {
      return { destructive: true, reason: `磁盘格式化（${segment}）` };
    }
    if (head === 'git') {
      const sub = rest[0];
      if (sub === 'push') return { destructive: true, reason: 'git push（推送到远端）' };
      if (sub === 'reset' && isFlag(rest, '--hard')) return { destructive: true, reason: 'git reset --hard' };
      if (sub === 'clean' && rest.some((flag) => /^-[a-z]*[fdx]/.test(flag))) {
        return { destructive: true, reason: 'git clean（删除未跟踪文件）' };
      }
      if (sub === 'branch' && isFlag(rest, '-d')) return { destructive: true, reason: 'git branch -D（强制删除分支）' };
    }
    if ((head === 'npm' || head === 'yarn' || head === 'pnpm') && rest[0] === 'publish') {
      return { destructive: true, reason: `${head} publish（发布包）` };
    }
    if (head === 'set-executionpolicy') {
      return { destructive: true, reason: '修改 PowerShell 执行策略' };
    }
    if (head === 'shutdown' || head === 'restart-computer' || head === 'stop-computer' || head === 'reboot') {
      return { destructive: true, reason: `系统关机/重启（${segment}）` };
    }
  }
  return { destructive: false };
}

const INTERACTIVE_PROGRAMS = new Set([
  'vim', 'vi', 'vimdiff', 'nano', 'emacs', 'pico', 'less', 'more', 'top', 'htop', 'man', 'ftp', 'telnet',
]);
const REPL_PROGRAMS = new Set(['python', 'python3', 'node', 'irb', 'psql', 'mysql', 'mongo', 'sqlite3']);

export function detectInteractive(command: string): InteractiveClassification {
  if (/\bRead-Host\b/i.test(command)) {
    return { interactive: true, reason: 'Read-Host 会等待交互输入' };
  }
  for (const segment of splitSegments(command)) {
    const tokens = tokenize(segment);
    if (tokens.length === 0) continue;
    const head = tokens[0].toLowerCase().replace(/\.exe$/, '');
    if (INTERACTIVE_PROGRAMS.has(head)) {
      return { interactive: true, reason: `${head} 是交互式程序，会阻塞非交互执行` };
    }
    if (REPL_PROGRAMS.has(head) && tokens.length === 1) {
      return { interactive: true, reason: `${head} 无参数会进入交互式 REPL` };
    }
  }
  return { interactive: false };
}
