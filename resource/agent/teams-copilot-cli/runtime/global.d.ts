// 浏览器注入的运行时全局变量类型声明
declare global {
  interface Window {
    __copilotIsStreaming?: boolean;
    __copilotBuffer?: string;
  }
}

export {};
