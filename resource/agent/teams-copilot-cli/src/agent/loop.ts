import type { AgentConfig } from '../types.js';
import type { ChatSession, ChatTurnResult, Provider } from '../provider/types.js';
import type { PermissionGate } from './permissions.js';
import type { ToolCall } from './protocol.js';
import {
  buildCorrectionMessage,
  formatToolResults,
  parseReply,
  type ProtocolToolResult,
} from './protocol.js';
import type { ToolRegistry } from './tools/registry.js';
import type { AuditLogger } from './audit.js';
import { buildProtocolPrompt, buildTaskMessage, type WorkspaceInfo } from './system-prompt.js';

const MAX_CORRECTIONS = 2;
const MAX_FORGETTING_RECOVERIES = 1;
const DEFAULT_SESSION_CHAR_BUDGET = 40000;
const TOOL_TIMEOUT_MS = 300000;
const SEND_RETRIES = 2;

export interface AgentUi {
  onCommentary?: (text: string) => void;
  onToolCall?: (call: ToolCall) => void;
  onToolResult?: (call: ToolCall, result: ProtocolToolResult) => void;
  onStatus?: (message: string) => void;
  streamChunk?: (chunk: string) => void;
}

export interface AgentDeps {
  provider: Provider;
  registry: ToolRegistry;
  gate: PermissionGate;
  workspace: WorkspaceInfo;
  config: AgentConfig;
  ui?: AgentUi;
  audit?: AuditLogger;
}

export interface AgentRunResult {
  summary: string;
  iterations: number;
  actions: string[];
}

export class AgentMaxIterationsError extends Error {
  readonly actions: string[];
  constructor(maxIterations: number, actions: string[]) {
    super(`Agent 达到最大迭代次数（${maxIterations}）仍未完成。已执行动作：\n${actions.map((action) => `- ${action}`).join('\n') || '(无)'}`);
    this.name = 'AgentMaxIterationsError';
    this.actions = actions;
  }
}

export class AgentProtocolError extends Error {
  readonly raw: string;
  constructor(problems: string[], raw: string) {
    super(`模型多次未能输出可解析的工具调用格式：\n${problems.join('\n')}\n\n原始回复：\n${raw}`);
    this.name = 'AgentProtocolError';
    this.raw = raw;
  }
}

interface LoopSession {
  session: ChatSession;
  turnsUsed: number;
  charsUsed: number;
}

export async function runAgent(task: string, deps: AgentDeps): Promise<AgentRunResult> {
  const { provider, registry, gate, workspace, config, ui, audit } = deps;
  const capabilities = provider.capabilities();
  const schemas = registry.schemas();
  const actions: string[] = [];
  let lastSendAt = 0;

  const state: LoopSession = { session: await provider.createSession(), turnsUsed: 0, charsUsed: 0 };

  async function send(message: string, stream = false): Promise<ChatTurnResult> {
    // Add up to 30% random jitter to the send interval so the cadence doesn't look
    // like a fixed-rate bot to the Copilot tenant's abuse detection (ADR-0007).
    const interval = config.minSendIntervalMs;
    const jitter = interval > 0 ? Math.floor(Math.random() * interval * 0.3) : 0;
    const wait = interval + jitter - (Date.now() - lastSendAt);
    if (wait > 0) await sleep(wait);
    let lastError: unknown;
    for (let attempt = 0; attempt <= SEND_RETRIES; attempt++) {
      try {
        const result = await sendChunked(state.session, message, capabilities.maxMessageChars, stream ? ui?.streamChunk : undefined);
        lastSendAt = Date.now();
        state.turnsUsed += 1;
        state.charsUsed += message.length + result.text.length;
        return result;
      } catch (error) {
        lastError = error;
        ui?.onStatus?.(`发送失败（第 ${attempt + 1} 次）：${errorMessage(error)}`);
        if (attempt < SEND_RETRIES) await sleep(1000 * 2 ** attempt);
      }
    }
    throw lastError instanceof Error ? lastError : new Error(String(lastError));
  }

  async function injectProtocol(): Promise<void> {
    ui?.onStatus?.('注入协议提示词...');
    await send(buildProtocolPrompt(workspace, registry.list()));
  }

  const charBudget = config.sessionCharBudget ?? DEFAULT_SESSION_CHAR_BUDGET;

  function resultBudget(): number {
    return shrinkResultBudget(state.charsUsed, charBudget, capabilities.maxMessageChars);
  }

  /** Returns a context-recovery prefix for the next message when the session was rebuilt. */
  async function rotateSessionIfNeeded(force = false): Promise<string> {
    const nearTurnBudget = state.turnsUsed >= Math.max(4, config.maxTurnsPerConversation - 2);
    // Rotate proactively at 85% of the budget rather than waiting for overflow.
    const overCharBudget = state.charsUsed >= charBudget * 0.85;
    const healthy = await state.session.healthy().catch(() => false);
    if (!force && !nearTurnBudget && !overCharBudget && healthy) return '';
    ui?.onStatus?.(force
      ? '疑似协议遗忘：重建会话并重新注入协议...'
      : nearTurnBudget || overCharBudget
      ? `会话上下文逼近预算（轮次 ${state.turnsUsed}/${config.maxTurnsPerConversation}，字符 ${state.charsUsed}/${charBudget}），开新会话...`
      : '会话不健康（登录过期/页面刷新），重建会话...');
    try {
      await state.session.close();
    } catch {
      // best effort
    }
    state.session = await provider.createSession();
    state.turnsUsed = 0;
    state.charsUsed = 0;
    await injectProtocol();
    return [
      '会话已重建。原始任务：',
      task,
      '',
      '此前进度摘要（文件状态一律以磁盘为准，必要时重新 read_file）：',
      ...actions.slice(-15).map((action) => `- ${action}`),
      '',
    ].join('\n');
  }

  await injectProtocol();
  let pending = await send(buildTaskMessage(task), true);
  let corrections = 0;
  let forgettingRecoveries = 0;

  for (let iteration = 0; iteration < config.maxIterations; iteration++) {
    const parsed = parseReply(pending.text, schemas);

    if (parsed.kind === 'done') {
      return { summary: parsed.summary, iterations: iteration, actions };
    }

    if (parsed.kind === 'malformed') {
      corrections += 1;
      if (corrections > MAX_CORRECTIONS) {
        // Repeated malformed/free-form replies suggest the model forgot the protocol:
        // rebuild the session, re-inject the protocol, and give it a fresh budget once.
        if (forgettingRecoveries < MAX_FORGETTING_RECOVERIES) {
          forgettingRecoveries += 1;
          corrections = 0;
          const recovery = await rotateSessionIfNeeded(true);
          pending = await send(recovery + buildCorrectionMessage(parsed.problems));
          continue;
        }
        throw new AgentProtocolError(parsed.problems, parsed.raw);
      }
      ui?.onStatus?.(`回复格式无法解析，请求纠正（${corrections}/${MAX_CORRECTIONS}）...`);
      const recovery = await rotateSessionIfNeeded();
      pending = await send(recovery + buildCorrectionMessage(parsed.problems));
      continue;
    }

    corrections = 0;
    if (parsed.commentary) ui?.onCommentary?.(parsed.commentary);

    const results: ProtocolToolResult[] = [];
    for (const call of parsed.calls) {
      ui?.onToolCall?.(call);
      const tool = registry.get(call.name);
      if (!tool) {
        results.push({ name: call.name, ok: false, output: `Unknown tool: ${call.name}` });
        continue;
      }
      const decision = await gate.check(call, tool.risk);
      let result: ProtocolToolResult;
      if (!decision.allowed) {
        result = { name: call.name, ok: false, output: `操作被拒绝：${decision.reason ?? '权限不足'}` };
      } else {
        try {
          const toolResult = await withTimeout(
            tool.run(call.args, { projectRoot: workspace.projectRoot, report: ui?.onStatus }),
            TOOL_TIMEOUT_MS,
            `工具 ${call.name} 执行超时`,
          );
          result = { name: call.name, ok: toolResult.ok, exitCode: toolResult.exitCode, output: toolResult.output };
        } catch (error) {
          result = { name: call.name, ok: false, output: `工具执行异常：${errorMessage(error)}` };
        }
      }
      results.push(result);
      actions.push(describeAction(call, result));
      audit?.({
        tool: call.name,
        target: describeTarget(call),
        allowed: decision.allowed,
        ok: result.ok,
        exitCode: result.exitCode,
        reason: decision.allowed ? undefined : decision.reason,
      });
      ui?.onToolResult?.(call, result);
    }

    const recovery = await rotateSessionIfNeeded();
    pending = await send(recovery + formatToolResults(results, { maxChars: resultBudget() }), true);
  }

  throw new AgentMaxIterationsError(config.maxIterations, actions);
}

/**
 * Shrinks the RESULT reinjection budget as the session fills up (ADR-0004): once past
 * 60% of the session char budget, halve the per-message budget (floored at 1500).
 */
export function shrinkResultBudget(
  charsUsed: number,
  charBudget: number,
  maxMessageChars: number,
): number {
  const ratio = charBudget > 0 ? charsUsed / charBudget : 0;
  const factor = ratio > 0.6 ? 0.5 : 1;
  return Math.max(1500, Math.floor(maxMessageChars * factor));
}

/** Splits an over-long message into parts; the model is told to reply OK until the last part. */
export async function sendChunked(
  session: ChatSession,
  message: string,
  maxMessageChars: number,
  onUpdate?: (chunk: string) => void,
): Promise<ChatTurnResult> {
  if (message.length <= maxMessageChars) {
    return session.send(message, { onUpdate });
  }
  const overhead = 80;
  const chunkSize = Math.max(500, maxMessageChars - overhead);
  const parts: string[] = [];
  for (let index = 0; index < message.length; index += chunkSize) {
    parts.push(message.slice(index, index + chunkSize));
  }
  let last: ChatTurnResult | null = null;
  for (const [index, part] of parts.entries()) {
    const isLast = index === parts.length - 1;
    const prefix = `[part ${index + 1}/${parts.length}]${isLast ? ' （已全部发送，请基于完整内容回复）' : ' （消息未发完，请只回复 OK 等待后续部分）'}\n`;
    last = await session.send(prefix + part, { onUpdate: isLast ? onUpdate : undefined });
  }
  return last!;
}

function describeTarget(call: ToolCall): string {
  return typeof call.args.path === 'string' ? call.args.path
    : typeof call.args.command === 'string' ? call.args.command.slice(0, 80)
    : typeof call.args.pattern === 'string' ? call.args.pattern
    : typeof call.args.subcommand === 'string' ? call.args.subcommand
    : '';
}

function describeAction(call: ToolCall, result: ProtocolToolResult): string {
  return `${call.name}(${describeTarget(call)}) → ${result.ok ? 'ok' : `失败: ${result.output.slice(0, 120)}`}`;
}

function withTimeout<T>(promise: Promise<T>, ms: number, message: string): Promise<T> {
  return new Promise<T>((resolvePromise, rejectPromise) => {
    const timer = setTimeout(() => rejectPromise(new Error(message)), ms);
    promise.then(
      (value) => { clearTimeout(timer); resolvePromise(value); },
      (error) => { clearTimeout(timer); rejectPromise(error); },
    );
  });
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolvePromise) => setTimeout(resolvePromise, ms));
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
