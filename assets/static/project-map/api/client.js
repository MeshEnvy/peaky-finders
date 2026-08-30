// @ts-check

/**
 * @param {string} url
 * @param {RequestInit & { signal?: AbortSignal }} [opts]
 */
export async function apiFetch(url, opts = {}) {
  const resp = await fetch(url, opts)
  if (!resp.ok) {
    const text = await resp.text().catch(() => '')
    throw new Error(text || `HTTP ${resp.status}`)
  }
  const ct = resp.headers.get('content-type') || ''
  if (ct.includes('application/json')) return resp.json()
  return resp
}

/** @param {string} url @param {unknown} body */
export async function apiJson(url, method, body) {
  return apiFetch(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body != null ? JSON.stringify(body) : undefined,
  })
}
