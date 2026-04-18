import { useState } from 'react'
import UploadForm from './components/UploadForm'
import ChatWindow from './components/ChatWindow'

export default function App() {
  const [sessionId, setSessionId] = useState(null)

  return sessionId
    ? <ChatWindow sessionId={sessionId} />
    : <UploadForm onStart={setSessionId} />
}
