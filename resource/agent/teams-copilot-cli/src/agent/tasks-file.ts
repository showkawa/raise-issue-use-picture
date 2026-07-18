export interface TaskItem {
  /** 0-based line index in the file. */
  line: number;
  /** Explicit id like "T1" when the line matches "T1: ...", otherwise the 1-based ordinal. */
  id: string;
  description: string;
  done: boolean;
}

const CHECKBOX_LINE = /^(\s*)- \[( |x|X)\] (.+)$/;
const TASK_ID = /^([A-Za-z]+\d+)\s*[:：]\s*(.*)$/;
// Lines that begin like a checkbox bullet but don't parse as a valid task
// (wrong bullet, missing space, empty/multi-char brackets, empty description).
const CHECKBOX_LIKE = /^\s*[-*+]\s*\[[^\]]*\]/;

export interface MalformedTaskLine {
  /** 0-based line index. */
  line: number;
  text: string;
}

/**
 * Finds lines that look like an intended task checkbox but are not parseable, so the
 * caller can surface them instead of silently dropping tasks.
 */
export function findMalformedTaskLines(content: string): MalformedTaskLine[] {
  const malformed: MalformedTaskLine[] = [];
  const lines = content.split(/\r?\n/);
  for (const [index, line] of lines.entries()) {
    if (CHECKBOX_LINE.test(line)) continue;
    if (CHECKBOX_LIKE.test(line)) {
      malformed.push({ line: index, text: line.trim() });
    }
  }
  return malformed;
}

export function parseTasks(content: string): TaskItem[] {
  const tasks: TaskItem[] = [];
  const lines = content.split(/\r?\n/);
  for (const [index, line] of lines.entries()) {
    const match = CHECKBOX_LINE.exec(line);
    if (!match) continue;
    const body = match[3].trim();
    const idMatch = TASK_ID.exec(body);
    tasks.push({
      line: index,
      id: idMatch ? idMatch[1] : String(tasks.length + 1),
      description: idMatch ? idMatch[2] : body,
      done: match[2].toLowerCase() === 'x',
    });
  }
  return tasks;
}

/** Returns the file content with the given task's checkbox marked done. */
export function markTaskDone(content: string, task: TaskItem): string {
  const newline = content.includes('\r\n') ? '\r\n' : '\n';
  const lines = content.split(/\r?\n/);
  const line = lines[task.line];
  if (line === undefined || !CHECKBOX_LINE.test(line)) {
    throw new Error(`Task line ${task.line + 1} is not a checkbox line anymore`);
  }
  lines[task.line] = line.replace(/- \[ \]/, '- [x]');
  return lines.join(newline);
}
