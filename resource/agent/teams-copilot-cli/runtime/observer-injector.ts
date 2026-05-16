/**
 * runtime/observer-injector.ts
 * ⚠️ DEPRECATED: 此文件已不再使用。
 * 
 * 原设计：注入 MutationObserver 监听流式输出，在内存中拼接完整 Markdown。
 * 当前实现：改用直接 DOM 轮询策略（pollCopilotResponse），更简单可靠。
 * 
 * 保留此文件仅作参考，新代码不应依赖它。
 */

export const OBSERVER_SCRIPT = `
  // 此脚本已废弃，请使用直接 DOM 轮询策略
  console.warn('[Observer] This script is deprecated. Use direct DOM polling instead.');
`;
