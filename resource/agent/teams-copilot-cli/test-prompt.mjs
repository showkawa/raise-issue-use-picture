// Modified debug-robust-injection.mjs with new prompt
import { CDPBridge } from '@jackwener/opencli/browser/cdp';

async function debugRobustInjection() {
  const bridge = new CDPBridge();
  const page = await bridge.connect({ cdpEndpoint: 'http://localhost:9222' });
  
  const res = await fetch('http://localhost:9222/json/list');
  const targets = await res.json();
  const iframeTarget = targets.find(
    t => t.url?.includes('outlook.office.com/hosted/semanticoverview')
  );
  
  if (!iframeTarget) {
    console.log('No Copilot iframe found.');
    process.exit(1);
  }
  
  const ws = new WebSocket(iframeTarget.webSocketDebuggerUrl);
  let msgId = 0;
  const pending = new Map();
  
  const cdpSend = (method, params) => {
    return new Promise((resolve, reject) => {
      const id = ++msgId;
      pending.set(id, { resolve, reject });
      ws.send(JSON.stringify({ id, method, params }));
    });
  };
  
  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      if (msg.error) reject(new Error(msg.error.message));
      else resolve(msg.result ?? {});
    }
  };
  
  await new Promise((resolve, reject) => {
    ws.onopen = resolve;
    ws.onerror = (e) => reject(new Error('WebSocket error'));
    setTimeout(() => reject(new Error('Timeout')), 5000);
  });
  
  await cdpSend('Runtime.enable');
  console.log('Connected to iframe CDP.');
  
  const testText = '你提供了哪些适合编程的模型';
  console.log('Prompt:', testText);
  
  // Step 1: Clear input
  console.log('\nStep 1: Clearing...');
  await cdpSend('Runtime.evaluate', {
    expression: `(() => {
      const el = document.querySelector('[contenteditable="true"]');
      if (!el) return 'NOT_FOUND';
      el.focus();
      document.execCommand('selectAll', false, null);
      document.execCommand('delete', false, null);
      return { cleared: true, textAfterClear: el.textContent };
    })()`,
    returnByValue: true
  });
  
  await new Promise(r => setTimeout(r, 500));
  
  // Step 2: Inject text
  console.log('Step 2: Injecting...');
  const injectResult = await cdpSend('Runtime.evaluate', {
    expression: `(() => {
      const el = document.querySelector('[contenteditable="true"]');
      if (!el) return 'NOT_FOUND';
      el.focus();
      document.execCommand('insertText', false, '${testText}');
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
      return {
        textContent: el.textContent,
        textLength: (el.textContent || '').length
      };
    })()`,
    returnByValue: true
  });
  console.log('Injected:', JSON.stringify(injectResult));
  
  // Step 3: Verify
  console.log('Step 3: Verifying...');
  await new Promise(r => setTimeout(r, 2000));
  
  const verifyResult = await cdpSend('Runtime.evaluate', {
    expression: `(() => {
      const el = document.querySelector('[contenteditable="true"]');
      if (!el) return 'NOT_FOUND';
      return { text: el.textContent, len: (el.textContent || '').length };
    })()`,
    returnByValue: true
  });
  console.log('Verify:', JSON.stringify(verifyResult));
  
  // Step 4: Try pressing Enter to send
  if (verifyResult.value?.len > 0) {
    console.log('Step 4: Sending via Enter key...');
    
    // Focus the input first
    await cdpSend('Runtime.evaluate', {
      expression: `(() => {
        const el = document.querySelector('[contenteditable="true"]');
        if (el) el.focus();
      })()`
    });
    
    await new Promise(r => setTimeout(r, 500));
    
    // Press Enter
    await cdpSend('Input.dispatchKeyEvent', {
      type: 'keyDown',
      key: 'Enter',
      code: 'Enter',
      windowsVirtualKeyCode: 13,
      nativeVirtualKeyCode: 13
    });
    await cdpSend('Input.dispatchKeyEvent', {
      type: 'keyUp',
      key: 'Enter',
      code: 'Enter',
      windowsVirtualKeyCode: 13,
      nativeVirtualKeyCode: 13
    });
    
    console.log('Enter sent, waiting for response...');
    
    // Step 5: Poll for response
    let lastLength = 0;
    let stableCount = 0;
    
    for (let i = 0; i < 30; i++) {
      await new Promise(r => setTimeout(r, 3000));
      
      const check = await cdpSend('Runtime.evaluate', {
        expression: `(() => {
          const main = document.querySelector('[role="main"]');
          const text = main ? main.innerText : '';
          return { length: text.length, tail: text.substring(Math.max(0, text.length - 2000)) };
        })()`,
        returnByValue: true
      });
      
      const val = check.value;
      if (val && val.length > lastLength + 5) {
        console.log('Growing: ' + val.length + ' chars');
        lastLength = val.length;
        stableCount = 0;
      } else {
        stableCount++;
      }
      
      if (val && val.length > 50 && stableCount >= 3) {
        console.log('\n========== COPILOT RESPONSE ==========');
        console.log(val.tail);
        console.log('======================================');
        break;
      }
      
      if (i === 29) {
        console.log('\n=== TIMEOUT ===');
        console.log('Last state length:', val?.length);
        console.log(val?.tail || '(empty)');
      }
    }
  } else {
    console.log('Text injection failed!');
  }
  
  ws.close();
  process.exit(0);
}

debugRobustInjection().catch(err => {
  console.error('Error:', err);
  process.exit(1);
});
