import { useState } from 'react'
import UploadForm from './components/UploadForm'
import ChatWindow from './components/ChatWindow'
import LoginForm from './components/LoginForm'
import { getToken, clearToken } from './auth'
import { AuthError } from './api'

export default function App() {
  const [authenticated, setAuthenticated] = useState(() => !!getToken())
  const [sessionId, setSessionId] = useState(null)

  function handleAuthError(err) {
    if (err instanceof AuthError) {
      clearToken()
      setAuthenticated(false)
      setSessionId(null)
    }
  }

  if (!authenticated) {
    return <LoginForm onLogin={() => setAuthenticated(true)} />
  }

  return sessionId
    ? <ChatWindow sessionId={sessionId} onAuthError={handleAuthError} />
    : <UploadForm onStart={setSessionId} onAuthError={handleAuthError} />
}
