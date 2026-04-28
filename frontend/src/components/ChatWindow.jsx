import { useState, useEffect, useRef } from 'react'
import MessageBubble from './MessageBubble'
import HitlCard from './HitlCard'
import Spinner from './Spinner'
import { getSessionState, respondHitl, getDownloadUrl, subscribeToSession } from '../api'

export default function ChatWindow({ sessionId }) {
  const [phase, setPhase] = useState('running')
  const [messages, setMessages] = useState([])
  const [hitlData, setHitlData] = useState(null)
  const [error, setError] = useState(null)
  const [currentStep, setCurrentStep] = useState('Iniciando...')
  const bottomRef = useRef(null)

  // Hidratación inicial — recupera estado en caso de recarga de página
  useEffect(() => {
    getSessionState(sessionId)
      .then(data => {
        setMessages(data.messages || [])
        setPhase(data.phase)
        if (data.hitl_data) setHitlData(data.hitl_data)
        if (data.error) setError(data.error)
        if (data.phase !== 'running') setCurrentStep('')
      })
      .catch(() => {})
  }, [sessionId])

  // Suscripción SSE — reemplaza el polling cada 2s
  useEffect(() => {
    const cleanup = subscribeToSession(
      sessionId,
      (event) => {
        if (event.type === 'agent_start') {
          setCurrentStep(event.message || '')
        }
        if (event.type === 'agent_end') {
          setCurrentStep('')
        }
        if (event.type === 'message') {
          setMessages(prev => [...prev, { role: event.role, content: event.content }])
        }
        if (event.type === 'hitl_required') {
          setCurrentStep('')
          setHitlData(event.hitl_data)
          setPhase('awaiting_hitl')
        }
        if (event.type === 'completed') {
          setCurrentStep('')
          setPhase('completed')
        }
        if (event.type === 'error') {
          setCurrentStep('')
          setPhase('error')
          setError(event.message)
        }
      },
      () => {
        // SSE cerró inesperadamente — leer estado una vez para sincronizar
        getSessionState(sessionId)
          .then(data => {
            setPhase(data.phase)
            if (data.error) setError(data.error)
          })
          .catch(() => {})
      }
    )
    return cleanup
  }, [sessionId])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, hitlData, phase, currentStep])

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
              {phase === 'running' && (currentStep || 'Procesando')}
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
