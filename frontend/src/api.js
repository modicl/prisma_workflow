export async function startChat({ paciFile, materialFile, prompt }) {
  const form = new FormData()
  form.append('paci_file', paciFile)
  form.append('material_file', materialFile)
  form.append('prompt', prompt)
  form.append('school_id', 'colegio_demo')

  const res = await fetch('/chat/start', { method: 'POST', body: form })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`Error ${res.status}: ${text}`)
  }
  return res.json()
}

export async function getSessionState(sessionId) {
  const res = await fetch(`/chat/${sessionId}/state`)
  if (!res.ok) throw new Error(`Error ${res.status}`)
  return res.json()
}

export async function respondHitl(sessionId, { approved, reason, agentToRetry }) {
  const res = await fetch(`/chat/${sessionId}/hitl`, {
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
  return `/chat/${sessionId}/download`
}

export function subscribeToSession(sessionId, onEvent, onError) {
  const source = new EventSource(`/chat/${sessionId}/stream`)
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
