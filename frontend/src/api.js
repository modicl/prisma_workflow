import { getToken } from './auth'

export class AuthError extends Error {
  constructor() {
    super('Sesión expirada. Por favor, inicia sesión nuevamente.')
    this.name = 'AuthError'
  }
}

function authHeaders(extra = {}) {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}`, ...extra } : extra
}

async function authFetch(url, options = {}) {
  const res = await fetch(url, {
    ...options,
    headers: { ...authHeaders(), ...options.headers },
  })
  if (res.status === 401) throw new AuthError()
  return res
}

export async function login(email, password) {
  const res = await fetch('/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.message || `Error ${res.status}`)
  }
  const data = await res.json()
  return data.access_token
}

export async function startChat({ paciFile, materialFile, prompt }) {
  const form = new FormData()
  form.append('paci_file', paciFile)
  form.append('material_file', materialFile)
  form.append('prompt', prompt)
  form.append('school_id', 'colegio_demo')

  const res = await authFetch('/chat/start', { method: 'POST', body: form })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`Error ${res.status}: ${text}`)
  }
  return res.json()
}

export async function getSessionState(sessionId) {
  const res = await authFetch(`/chat/${sessionId}/state`)
  if (!res.ok) throw new Error(`Error ${res.status}`)
  return res.json()
}

export async function respondHitl(sessionId, { approved, reason, agentToRetry }) {
  const res = await authFetch(`/chat/${sessionId}/hitl`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      approved,
      reason: reason || null,
      agent_to_retry: agentToRetry || null,
    }),
  })
  if (!res.ok) throw new Error(`Error ${res.status}`)
  return res.json()
}

export function getDownloadUrl(sessionId) {
  const token = getToken()
  return `/chat/${sessionId}/download${token ? `?token=${encodeURIComponent(token)}` : ''}`
}

export function subscribeToSession(sessionId, onEvent, onError) {
  const token = getToken()
  const url = `/chat/${sessionId}/stream${token ? `?token=${encodeURIComponent(token)}` : ''}`
  const source = new EventSource(url)
  source.onmessage = (e) => {
    const event = JSON.parse(e.data)
    if (event.type !== 'ping') onEvent(event)
  }
  source.onerror = () => {
    onError?.()
    source.close()
  }
  return () => source.close()
}
