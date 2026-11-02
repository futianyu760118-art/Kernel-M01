import { createHmac } from 'node:crypto';

function endpointOf(url) {
  const base = String(url || '').replace(/\/$/, '');
  return base.endsWith('/api/m01/personnel/offboard') ? base : `${base}/api/m01/personnel/offboard`;
}

export function buildOffboardSyncRequest({ url, secret, event, timestamp = Date.now() }) {
  const body = JSON.stringify(event || {});
  const ts = String(timestamp);
  const signature = createHmac('sha256', String(secret || ''))
    .update(ts + '.' + body)
    .digest('hex');
  return {
    url: endpointOf(url),
    body,
    headers: {
      'Content-Type': 'application/json',
      'X-M01-Timestamp': ts,
      'X-M01-Signature': `sha256=${signature}`
    }
  };
}

export async function notifyEbmsOffboard({ url, secret, event, fetchImpl = fetch, timeoutMs = 5000 }) {
  if (!url || !secret) return { ok: false, status: 'not_configured', error: 'EBMS离职同步未配置' };
  const request = buildOffboardSyncRequest({ url, secret, event });
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetchImpl(request.url, {
      method: 'POST',
      headers: request.headers,
      body: request.body,
      signal: controller.signal
    });
    const text = await response.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = { raw: text }; }
    if (!response.ok) return { ok: false, status: 'failed', http_status: response.status, error: data?.error || `HTTP ${response.status}` };
    return { ok: true, status: 'synced', data };
  } catch (error) {
    return { ok: false, status: 'failed', error: error?.name === 'AbortError' ? '请求EBMS超时' : error.message };
  } finally {
    clearTimeout(timer);
  }
}
