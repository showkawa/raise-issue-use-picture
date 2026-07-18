import { describe, expect, it } from 'vitest';
import { findMalformedTaskLines, markTaskDone, parseTasks } from '../src/agent/tasks-file.js';

const sample = [
  '# 项目任务',
  '',
  '## 任务清单',
  '',
  '- [ ] T1: 搭建项目骨架',
  '- [x] T2: 实现登录',
  '- [ ] 无编号任务',
  '',
  '普通文本 - [ ] 不是任务',
].join('\n');

describe('parseTasks', () => {
  it('parses checkbox lines with ids, ordinals and done state', () => {
    const tasks = parseTasks(sample);
    expect(tasks).toHaveLength(3);
    expect(tasks[0]).toMatchObject({ id: 'T1', description: '搭建项目骨架', done: false, line: 4 });
    expect(tasks[1]).toMatchObject({ id: 'T2', done: true });
    expect(tasks[2]).toMatchObject({ id: '3', description: '无编号任务', done: false });
  });

  it('returns empty for files without checkboxes', () => {
    expect(parseTasks('# nothing here')).toEqual([]);
  });
});

describe('findMalformedTaskLines', () => {
  it('does not flag valid tasks or ordinary prose', () => {
    expect(findMalformedTaskLines(sample)).toEqual([]);
  });

  it('flags checkbox-like lines that would be silently dropped', () => {
    const content = [
      '- [ ] T1: 正常任务',
      '- [] T2: 缺少空格',
      '-[ ] T3: 破折号后无空格',
      '* [ ] T4: 错误的项目符号',
      '- [ ]T5: 括号后无空格',
      '普通文本 - [ ] 不是任务',
    ].join('\n');
    const malformed = findMalformedTaskLines(content);
    expect(malformed.map((item) => item.line)).toEqual([1, 2, 3, 4]);
    expect(malformed[0].text).toBe('- [] T2: 缺少空格');
  });
});

describe('markTaskDone', () => {
  it('checks exactly the requested task line', () => {
    const tasks = parseTasks(sample);
    const updated = markTaskDone(sample, tasks[0]);
    expect(updated).toContain('- [x] T1: 搭建项目骨架');
    expect(updated).toContain('- [ ] 无编号任务');
  });

  it('preserves CRLF newlines', () => {
    const crlf = '- [ ] T1: a\r\n- [ ] T2: b\r\n';
    const tasks = parseTasks(crlf);
    const updated = markTaskDone(crlf, tasks[1]);
    expect(updated).toBe('- [ ] T1: a\r\n- [x] T2: b\r\n');
  });

  it('throws when the line is no longer a checkbox', () => {
    const tasks = parseTasks(sample);
    expect(() => markTaskDone('# rewritten file', tasks[0])).toThrow('not a checkbox');
  });
});
