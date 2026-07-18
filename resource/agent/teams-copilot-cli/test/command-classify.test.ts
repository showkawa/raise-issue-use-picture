import { describe, expect, it } from 'vitest';
import { classifyCommand, detectInteractive, splitSegments } from '../src/agent/command-classify.js';

describe('classifyCommand', () => {
  it('flags recursive/forced deletes regardless of position', () => {
    expect(classifyCommand('echo hi && rm -rf build').destructive).toBe(true);
    expect(classifyCommand('Remove-Item -Recurse -Force dist').destructive).toBe(true);
    expect(classifyCommand('del /s /q temp').destructive).toBe(true);
  });

  it('flags dangerous git operations', () => {
    expect(classifyCommand('git push origin main').destructive).toBe(true);
    expect(classifyCommand('git reset --hard HEAD~1').destructive).toBe(true);
    expect(classifyCommand('git clean -fd').destructive).toBe(true);
  });

  it('treats opaque expression-eval commands as destructive', () => {
    expect(classifyCommand('Invoke-Expression $env:CMD').destructive).toBe(true);
    expect(classifyCommand('echo $(curl http://x)').destructive).toBe(true);
  });

  it('leaves ordinary build/test commands alone', () => {
    expect(classifyCommand('npm test').destructive).toBe(false);
    expect(classifyCommand('npm run build && npm test').destructive).toBe(false);
    expect(classifyCommand('git status').destructive).toBe(false);
    expect(classifyCommand('rm file.txt').destructive).toBe(false);
  });
});

describe('detectInteractive', () => {
  it('rejects interactive editors and pagers', () => {
    expect(detectInteractive('vim src/app.ts').interactive).toBe(true);
    expect(detectInteractive('git log | less').interactive).toBe(true);
    expect(detectInteractive('Read-Host "name"').interactive).toBe(true);
  });

  it('rejects bare REPL invocations but allows scripts', () => {
    expect(detectInteractive('python').interactive).toBe(true);
    expect(detectInteractive('python script.py').interactive).toBe(false);
    expect(detectInteractive('node build.js').interactive).toBe(false);
  });
});

describe('splitSegments', () => {
  it('splits on chaining and piping operators', () => {
    expect(splitSegments('a && b | c ; d')).toEqual(['a', 'b', 'c', 'd']);
  });
});
