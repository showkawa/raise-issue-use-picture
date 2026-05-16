// 浏览器注入的运行时全局变量类型声明
// ⚠️ 注意：__copilotBuffer 和 __copilotIsStreaming 已废弃
// 当前使用直接 DOM 轮询策略，不再依赖这些变量
declare global {
  interface Window {
    // 已废弃，保留仅作兼容
    __copilotIsStreaming?: boolean;
    __copilotBuffer?: string;
  }
}

export {};
