export async function fetchJson(url, options = {}) {
  const timeoutMs = Number(options.timeoutMs || 10000);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, {
      ...options,
      signal: controller.signal,
    });
    if (!response.ok) throw new Error(`HTTP ${response.status} from ${url}`);
    return response.json();
  } catch (error) {
    if (error?.name === "AbortError") {
      throw new Error(`Timed out fetching ${url}`);
    }
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

export function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export async function listTabs(port) {
  return fetchJson(`http://127.0.0.1:${port}/json/list`);
}

export function connectCdp(url, { onMessage, onClose, callTimeoutMs = 15000 } = {}) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(url);
    let settled = false;
    const timeout = setTimeout(
      () => {
        if (settled) return;
        settled = true;
        ws.close();
        reject(new Error("Timed out connecting to Chrome DevTools"));
      },
      10000,
    );

    ws.onerror = () => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      reject(new Error("Failed to connect to Chrome DevTools"));
    };

    ws.onopen = () => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      let nextId = 1;
      const pending = new Map();

      ws.onmessage = (event) => {
        const message = JSON.parse(event.data);
        onMessage?.(message);
        if (!message.id) return;
        const request = pending.get(message.id);
        if (!request) return;
        pending.delete(message.id);
        clearTimeout(request.timeout);
        if (message.error) request.reject(new Error(JSON.stringify(message.error)));
        else request.resolve(message.result);
      };

      ws.onclose = () => {
        for (const request of pending.values()) {
          clearTimeout(request.timeout);
          request.reject(new Error("Chrome DevTools connection closed"));
        }
        pending.clear();
        onClose?.();
      };

      resolve({
        call(method, params = {}) {
          if (ws.readyState !== WebSocket.OPEN) {
            return Promise.reject(new Error("Chrome DevTools connection is not open"));
          }
          const id = nextId;
          nextId += 1;
          return new Promise((resolveCall, rejectCall) => {
            const callTimeout = setTimeout(() => {
              pending.delete(id);
              rejectCall(new Error(`Timed out calling Chrome DevTools method ${method}`));
            }, callTimeoutMs);
            pending.set(id, {
              resolve: resolveCall,
              reject: rejectCall,
              timeout: callTimeout,
            });
            try {
              ws.send(JSON.stringify({ id, method, params }));
            } catch (error) {
              clearTimeout(callTimeout);
              pending.delete(id);
              rejectCall(error);
            }
          });
        },
        close() {
          ws.close();
        },
      });
    };
  });
}
