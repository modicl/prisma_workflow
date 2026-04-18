import { useState, useRef } from 'react'
import { startChat } from '../api'

function FileDropZone({ label, file, onChange }) {
  const inputRef = useRef(null)

  function handleDrop(e) {
    e.preventDefault()
    const f = e.dataTransfer.files[0]
    if (f) onChange(f)
  }

  return (
    <div className="mb-4">
      <label className="block text-sm font-medium text-gray-700 mb-1">{label}</label>
      <div
        onDrop={handleDrop}
        onDragOver={e => e.preventDefault()}
        onClick={() => inputRef.current.click()}
        className="border-2 border-dashed border-gray-300 rounded-xl p-5 text-center cursor-pointer hover:border-blue-400 hover:bg-blue-50 transition-colors"
      >
        {file ? (
          <span className="text-sm text-green-600 font-medium">✓ {file.name}</span>
        ) : (
          <span className="text-sm text-gray-400">
            Arrastra o haz click — <span className="font-medium">.pdf</span> o <span className="font-medium">.docx</span>
          </span>
        )}
        <input
          ref={inputRef}
          type="file"
          accept=".pdf,.docx"
          className="hidden"
          onChange={e => onChange(e.target.files[0] || null)}
        />
      </div>
    </div>
  )
}

export default function UploadForm({ onStart }) {
  const [paciFile, setPaciFile] = useState(null)
  const [materialFile, setMaterialFile] = useState(null)
  const [prompt, setPrompt] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  async function handleSubmit(e) {
    e.preventDefault()
    if (!paciFile || !materialFile) {
      setError('Debes subir ambos archivos.')
      return
    }
    setLoading(true)
    setError('')
    try {
      const { session_id } = await startChat({ paciFile, materialFile, prompt })
      onStart(session_id)
    } catch (err) {
      setError(err.message)
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-blue-50 to-gray-100 flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl shadow-xl p-8 w-full max-w-md">
        <div className="text-center mb-8">
          <h1 className="text-3xl font-bold text-gray-900 tracking-tight">PRISMA</h1>
          <p className="text-sm text-gray-500 mt-1">Generador de Rúbricas Adaptadas para NEE</p>
        </div>

        <form onSubmit={handleSubmit}>
          <FileDropZone label="PACI del estudiante" file={paciFile} onChange={setPaciFile} />
          <FileDropZone label="Material base del curso" file={materialFile} onChange={setMaterialFile} />

          <div className="mb-6">
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Instrucción adicional{' '}
              <span className="text-gray-400 font-normal">(opcional)</span>
            </label>
            <textarea
              className="w-full border border-gray-300 rounded-xl px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 resize-none"
              rows={3}
              placeholder="Ej: Foco en comprensión lectora, actividades kinestésicas..."
              value={prompt}
              onChange={e => setPrompt(e.target.value)}
            />
          </div>

          {error && (
            <p className="text-red-500 text-sm mb-4 bg-red-50 rounded-lg px-3 py-2">{error}</p>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full bg-blue-600 text-white font-semibold py-3 rounded-xl hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {loading ? (
              <span className="flex items-center justify-center gap-2">
                <span className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
                Iniciando...
              </span>
            ) : (
              'Iniciar análisis ▶'
            )}
          </button>
        </form>
      </div>
    </div>
  )
}
