import type { InjectResult } from '../types.js';
import type { Frame } from 'playwright-core';

export async function injectText(
  frame: Frame,
  text: string,
  selector: string,
): Promise<InjectResult> {
  const locator = frame.locator(selector).first();
  try {
    await locator.click({ timeout: 10000 });
    await frame.page().keyboard.press(process.platform === 'darwin' ? 'Meta+A' : 'Control+A');
    await frame.page().keyboard.press('Backspace');
    await frame.page().keyboard.insertText(text);

    const contentMatches = await frame.evaluate(
      ({ sel, txt }) => {
        const el = document.querySelector(sel) as HTMLElement | null;
        return el?.textContent?.includes(txt) ?? false;
      },
      { sel: selector, txt: text },
    );

    if (!contentMatches) {
      throw new Error('Text verification failed');
    }
    return { success: true, method: 'insertText' };
  } catch {
    try {
      await locator.click({ timeout: 10000 });
      await frame.page().keyboard.press(process.platform === 'darwin' ? 'Meta+A' : 'Control+A');
      await frame.page().keyboard.press('Backspace');
      const result = await frame.evaluate(
        ({ sel, txt }) => {
          const el = document.querySelector(sel) as HTMLElement | null;
          if (!el) return false;
          el.focus();
          const dataTransfer = new DataTransfer();
          dataTransfer.setData('text/plain', txt);
          el.dispatchEvent(new ClipboardEvent('paste', {
            clipboardData: dataTransfer,
            bubbles: true,
            cancelable: true,
          }));
          return el.textContent?.includes(txt) ?? false;
        },
        { sel: selector, txt: text },
      );
      return result
        ? { success: true, method: 'clipboard' }
        : { success: false, method: 'clipboard', error: 'Text verification failed' };
    } catch (fallbackError: unknown) {
      const message = fallbackError instanceof Error ? fallbackError.message : String(fallbackError);
      return { success: false, method: 'clipboard', error: message };
    }
  }
}
