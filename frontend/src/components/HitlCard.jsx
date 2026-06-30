import { useState } from 'react'

function Accordion({ title, content }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="border border-amber-200 rounded-xl mb-2 overflow-hidden">
      <button
        onClick={() => setOpen(v => !v)}
        className="w-full text-left px-4 py-2.5 flex justify-between items-center text-sm font-medium text-amber-900 hover:bg-amber-100 transition-colors"
      >
        {title}
        <span className="text-xs">{open ? '▲' : '▼'}</span>
      </button>
      {open && (
        <div className="px-4 pb-3 pt-1 text-xs text-gray-700 whitespace-pre-wrap max-h-52 overflow-y-auto bg-white border-t border-amber-100">
          {content || '(sin datos)'}
        </div>
      )}
    </div>
  )
}

export default function HitlCard({ hitlData, onRespond }) {
  const [approved, setApproved] = useState(null)
  const [reason, setReason] = useState('')
  const [agentToRetry, setAgentToRetry] = useState(null)
  const [submitting, setSubmitting] = useState(false)

  const canSubmit =
    approved === true || (approved === false && reason.trim() && agentToRetry !== null)

  async function handleConfirm() {
    if (!canSubmit) return
    setSubmitting(true)
    await onRespond({
      approved,
      reason: approved ? null : reason,
      agentToRetry: approved ? null : agentToRetry,
    })
  }

  return (
    <div className="border-2 border-amber-400 bg-amber-50 rounded-2xl p-4 my-2">
      <p className="text-sm font-semibold text-amber-800 mb-3">
        ⚠ Revisión requerida — intento {hitlData.attempt} de {hitlData.max_attempts}
      </p>

      <Accordion title="📋 Análisis PACI (Agente 1)" content={hitlData.perfil_paci} />
      <Accordion title="📝 Planificación Adaptada (Agente 2)" content={hitlData.planificacion_adaptada} />

      <div className="flex gap-2 mt-4">
        <button
          onClick={() => { setApproved(true); setAgentToRetry(null); setReason('') }}
          className={`flex-1 py-2 rounded-xl text-sm font-medium transition-colors border ${
            approved === true
              ? 'bg-green-600 text-white border-green-600'
              : 'bg-white text-green-700 border-green-400 hover:bg-green-50'
          }`}
        >
          ✅ Aprobar
        </button>
        <button
          onClick={() => setApproved(false)}
          className={`flex-1 py-2 rounded-xl text-sm font-medium transition-colors border ${
            approved === false
              ? 'bg-red-500 text-white border-red-500'
              : 'bg-white text-red-500 border-red-400 hover:bg-red-50'
          }`}
        >
          ❌ Rechazar
        </button>
      </div>

      {approved === false && (
        <div className="mt-3 space-y-2">
          <textarea
            className="w-full border border-gray-300 rounded-xl px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none"
            rows={2}
            placeholder="Describe el problema encontrado (requerido)"
            value={reason}
            onChange={e => setReason(e.target.value)}
          />
          <p className="text-xs font-medium text-gray-600">¿Qué se debe corregir?</p>
          <div className="flex gap-2">
            {[
              { id: 1, label: 'Análisis del PACI' },
              { id: 2, label: 'Adaptación del material' },
            ].map(({ id, label }) => (
              <button
                key={id}
                onClick={() => setAgentToRetry(id)}
                className={`flex-1 py-1.5 rounded-lg text-xs font-medium transition-colors border ${
                  agentToRetry === id
                    ? 'bg-blue-600 text-white border-blue-600'
                    : 'bg-white text-gray-600 border-gray-300 hover:bg-gray-50'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      )}

      {approved !== null && (
        <button
          onClick={handleConfirm}
          disabled={!canSubmit || submitting}
          className="w-full mt-3 bg-blue-600 text-white font-semibold py-2.5 rounded-xl hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed text-sm transition-colors"
        >
          {submitting ? 'Enviando...' : 'Confirmar'}
        </button>
      )}
    </div>
  )
}
