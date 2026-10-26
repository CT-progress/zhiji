// 共享 API 与 SSE 辅助：从原 app.js 移植，业务逻辑不变
const ctx = { getAuthRequired: () => false, onUnauthorized: () => {} }
export function configureApi(patch) { Object.assign(ctx, patch) }

export function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

export async function api(path, options = {}) {
  const res = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...options })
  if (!res.ok) {
    if (res.status === 401 && ctx.getAuthRequired() && !path.startsWith('/api/auth/')) {
      ctx.onUnauthorized()
    }
    const data = await res.json().catch(() => ({}))
    const detail = data.detail || data.error || {}
    const msg = detail.message || detail.title || data.detail || '请求失败'
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg))
  }
  return res.json()
}

export async function readSSE(path, options, onEvent) {
  const res = await fetch(path, options)
  if (!res.ok) {
    if (res.status === 401 && ctx.getAuthRequired()) ctx.onUnauthorized()
    const data = await res.json().catch(() => ({}))
    const detail = data.detail || data.error || {}
    throw new Error(detail.message || detail.title || '请求失败')
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const parts = buffer.split('\n\n')
    buffer = parts.pop()
    for (const part of parts) {
      const line = part.trim()
      if (line.startsWith('data: ')) {
        try { onEvent(JSON.parse(line.slice(6))) } catch (e) { /* 忽略坏帧 */ }
      }
    }
  }
}

export function streamSSE(path, body, onEvent) {
  return readSSE(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }, onEvent)
}

export function streamGetSSE(path, onEvent) {
  return readSSE(path, { method: 'GET' }, onEvent)
}
