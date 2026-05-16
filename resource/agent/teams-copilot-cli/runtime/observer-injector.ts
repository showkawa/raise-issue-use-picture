/**
 * runtime/observer-injector.ts
 * 流式监听注入脚本 — 注入到 Teams 页面，在内存中拼接完整 Markdown，
 * 绕过虚拟滚动 DOM 回收问题。
 */

export const OBSERVER_SCRIPT = `
  window.__copilotBuffer = "";
  window.__copilotIsStreaming = false;

  const targetNode = document.body;
  const config = { childList: true, subtree: true, characterData: true };

  const observer = new MutationObserver(() => {
    // 检测 Stop generating 按钮是否存在，判断是否仍在流式输出
    const stopBtn = document.querySelector('[aria-label*="Stop generating"]');
    if (stopBtn) {
      window.__copilotIsStreaming = true;
    }

    // 寻找最后一条 Copilot 回复（结合 ARIA 和 class 猜测）
    const messages = document.querySelectorAll(
      '[data-conversation-role="app"], .message-body:last-child',
    );
    if (messages.length > 0) {
      const lastMsg = messages[messages.length - 1];
      // 每次全量覆盖，避免增量拼接的复杂性
      window.__copilotBuffer = lastMsg.innerText ?? '';
    }

    // Stop button 消失且已有内容，视为流式输出结束
    if (!stopBtn && window.__copilotBuffer.length > 0) {
      window.__copilotIsStreaming = false;
    }
  });

  observer.observe(targetNode, config);
`;
