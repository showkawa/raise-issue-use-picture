import type { StreamResult, CopilotConfig } from '../../types.js';
import type { Frame } from 'playwright-core';

export interface ExtractOptions {
  baseline?: string;
  onUpdate?: (chunk: string) => void;
}

export async function extractStream(
  frame: Frame,
  config: CopilotConfig,
  options: ExtractOptions = {},
): Promise<StreamResult> {
  const start = Date.now();
  let lastText = '';
  let lastEmitted = '';
  let stableCount = 0;
  const interval = config.timeouts.pollingInterval;
  const maxStable = Math.max(3, Math.ceil(2500 / interval));
  const timeout = config.timeouts.streaming;

  while (Date.now() - start < timeout) {
    const text = await readResponseText(frame, config.selectors.responseContainer, options.baseline);
    if (options.onUpdate && text.startsWith(lastEmitted) && text.length > lastEmitted.length) {
      const chunk = text.slice(lastEmitted.length);
      options.onUpdate(chunk);
      lastEmitted = text;
    }

    if (text === lastText && text.length > 0) {
      stableCount++;
      if (stableCount >= maxStable) {
        const truncated = isTruncated(text);
        return { text, truncated, duration: Date.now() - start };
      }
    } else {
      stableCount = 0;
    }
    lastText = text;
    await new Promise((r) => setTimeout(r, interval));
  }

  // Timeout: return what we have
  return { text: lastText, truncated: true, duration: Date.now() - start };
}

export async function readResponseText(
  frame: Frame,
  selector: string,
  baseline = '',
): Promise<string> {
  const raw = await frame.evaluate(
    (sel) => {
      const containers = Array.from(document.querySelectorAll(sel));
      const texts = containers
        .map((container) => (container instanceof HTMLElement
          ? container.innerText
          : container.textContent || '').trim())
        .filter(Boolean);
      return texts.length > 0 ? texts[texts.length - 1] : '';
    },
    selector,
  );

  const text = cleanCapturedText(raw);
  if (baseline && text.startsWith(baseline)) {
    return text.slice(baseline.length).trimStart();
  }
  return text;
}

/**
 * Normalizes text captured from the Copilot page: the code-block widget
 * HTML-escapes angle brackets and injects a language header plus a
 * line-number gutter into the rendered text.
 */
export function cleanCapturedText(text: string): string {
  const decoded = text
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&nbsp;|\u00a0/g, ' ')
    .replace(/&amp;/g, '&');
  return decoded
    .split('\n')
    .filter((line) => !/^\s*\d+\s*$/.test(line)
      && !/^\s*(plain text|plaintext|markdown|json|yaml|text)\s*$/i.test(line))
    .join('\n');
}

export function isTruncated(text: string): boolean {
  if (!text) return false;
  const trimmed = text.trim();
  const codeBlockCount = (trimmed.match(/```/g) || []).length;
  const codeBlockUnclosed = codeBlockCount % 2 !== 0;
  const endsMidClause = /[,，;；:：、]$/.test(trimmed);
  return codeBlockUnclosed || endsMidClause;
}
