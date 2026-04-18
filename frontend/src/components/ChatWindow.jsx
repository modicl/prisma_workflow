import { useState, useEffect, useRef } from 'react'
import MessageBubble from './MessageBubble'
import HitlCard from './HitlCard'
import Spinner from './Spinner'
import { getSessionState, respondHitl, getDownloadUrl } from '../api'

export default function ChatWindow({ sessionId }) {
  const [phase, setPhase] = useState('running')
  const [messages, setMessages] = useState([])
  const [hitlData, setHitlData] = useState(null)
  const [error, setError] = useState(null)
  const bottomRef = useRef(null)

  useEffect(() => {
    if (phase !== 'running') return

    const interval = setInterval(async () => {
      try {
        const data = await getSessionState(sessionId)
        setMessages(data.messages || [])
        if (data.hitl_data) setHitlData(data.hitl_data)
        if (data.error) setError(data.error)
        setPhase(data.phase)
      } catch (err) {
        setError(err.message)
        setPhase('error')
      }
    }, 2000)

    return () => clearInterval(interval)
  }, [phase, sessionId])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, hitlData, phase])

  async function handleHitlRespond(response) {
    await respondHitl(sessionId, response)
    setHitlData(null)
    setPhase('running')
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 to-gray-100 flex flex-col items-center p-4">
      <div className="w-full max-w-2xl bg-white rounded-2xl shadow-xl flex flex-col h-[90vh]">

        {/* Header */}
        <div className="px-6 py-4 border-b border-gray-100 flex-shrink-0">
          <h1 className="font-bold text-gray-900 text-lg">PRISMA — Flujo PACI</h1>
          <p className="text-xs text-gray-400 mt-0.5">
            Sesión {sessionId.slice(0, 8)}… ·{' '}
            <span className={
              phase === 'completed' ? 'text-green-500' :
              phase === 'error' ? 'text-red-500' :
              phase === 'awaiting_hitl' ? 'text-amber-500' :
              'text-blue-500'
            }>
              {phase === 'running' && 'Procesando'}
              {phase === 'awaiting_hitl' && 'Esperando revisión'}
              {phase === 'completed' && 'Completado'}
              {phase === 'error' && 'Error'}
            </span>
          </p>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-6 py-4">
          {messages.map((msg, i) => (
            <MessageBubble key={i} role={msg.role} content={msg.content} />
          ))}

          {phase === 'awaiting_hitl' && hitlData && (
            <HitlCard hitlData={hitlData} onRespond={handleHitlRespond} />
          )}

          {phase === 'running' && <Spinner />}

          {phase === 'completed' && (
            <div className="flex justify-center mt-6">
              <a
                href={getDownloadUrl(sessionId)}
                download
                className="bg-green-600 text-white font-semibold px-8 py-3 rounded-xl hover:bg-green-700 transition-colors text-sm shadow-md"
              >
                ⬇ Descargar Rúbrica (.docx)
              </a>
            </div>
          )}

          {phase === 'error' && (
            <div className="bg-red-50 border border-red-200 rounded-xl p-4 text-sm text-red-700 mt-2">
              ❌ {error || 'Ocurrió un error durante el procesamiento.'}
            </div>
          )}

          <div ref={bottomRef} />
        </div>
      </div>
    </div>
  )
}
