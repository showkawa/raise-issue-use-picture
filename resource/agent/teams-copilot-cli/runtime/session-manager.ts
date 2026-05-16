/**
 * runtime/session-manager.ts
 * 会话管理器 — 负责配置加载、Session 状态检测、认证状态维护。
 */

import fs from 'node:fs';
import path from 'node:path';
import { load as yamlLoad } from 'js-yaml';

/** config.yaml 的类型定义 */
export interface TeamsCopilotConfig {
  edge: {
    executablePath: string;
    userDataDir: string;
    debuggingPort: number;
  };
  copilot: {
    url: string;
    inputSelector: string;
    sendButtonSelector: string;
    messageSelector: string;
    timeout: number;
  };
}

/**
 * 加载 config.yaml 配置文件。
 * 从项目根目录向上查找，优先使用命令行参数 --config 指定的路径。
 */
export function loadConfig(configPath?: string): TeamsCopilotConfig {
  const resolvedPath =
    configPath ?? path.resolve(import.meta.dirname ?? process.cwd(), '..', 'config.yaml');

  if (!fs.existsSync(resolvedPath)) {
    throw new Error(
      `[SessionManager] 配置文件不存在: ${resolvedPath}\n` +
        '请确保 config.yaml 已按 config.yaml.example 创建。',
    );
  }

  const raw = fs.readFileSync(resolvedPath, 'utf-8');
  const config = yamlLoad(raw) as TeamsCopilotConfig;

  validateConfig(config);
  return config;
}

/** 配置项校验 */
function validateConfig(config: TeamsCopilotConfig): void {
  if (!config.edge?.executablePath) {
    throw new Error('[SessionManager] config.yaml 缺少 edge.executablePath');
  }
  if (!config.edge?.debuggingPort || config.edge.debuggingPort <= 0) {
    throw new Error('[SessionManager] config.yaml edge.debuggingPort 无效');
  }
  if (!config.copilot?.url) {
    throw new Error('[SessionManager] config.yaml 缺少 copilot.url');
  }
  if (!config.copilot?.inputSelector) {
    throw new Error('[SessionManager] config.yaml 缺少 copilot.inputSelector');
  }
  if (!config.copilot?.sendButtonSelector) {
    throw new Error('[SessionManager] config.yaml 缺少 copilot.sendButtonSelector');
  }
  if (!config.copilot?.messageSelector) {
    throw new Error('[SessionManager] config.yaml 缺少 copilot.messageSelector');
  }
  if (!config.copilot?.timeout || config.copilot.timeout <= 0) {
    throw new Error('[SessionManager] config.yaml copilot.timeout 无效');
  }
}

/**
 * 检测当前 Teams 会话是否有效（未被重定向到登录页）。
 */
export function isAuthenticated(url: string): boolean {
  const lower = url.toLowerCase();
  return !(
    lower.includes('login.microsoftonline.com') ||
    lower.includes('microsoft.com/login') ||
    lower.includes('microsoftonline.com/oauth2')
  );
}

/**
 * 错误码映射表 — 供 CLI 退出码使用。
 */
export const ERROR_CODES = {
  AUTH_EXPIRED: 77,
  COPILOT_UNAVAILABLE: 66,
  STREAMING_TIMEOUT: 88,
  CONFIG_ERROR: 99,
  UNKNOWN: 1,
} as const;

/** 根据错误消息映射退出码 */
export function mapErrorCode(message: string): number {
  if (message.includes('AUTH_EXPIRED')) return ERROR_CODES.AUTH_EXPIRED;
  if (message.includes('COPILOT_UNAVAILABLE')) return ERROR_CODES.COPILOT_UNAVAILABLE;
  if (message.includes('TIMEOUT') || message.includes('STREAMING_TIMEOUT'))
    return ERROR_CODES.STREAMING_TIMEOUT;
  if (message.includes('CONFIG')) return ERROR_CODES.CONFIG_ERROR;
  return ERROR_CODES.UNKNOWN;
}
