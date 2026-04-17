export default function Spinner() {
  return (
    <div className="flex items-center gap-2 text-gray-500 text-sm py-2 px-1">
      <div className="w-4 h-4 border-2 border-blue-500 border-t-transparent rounded-full animate-spin flex-shrink-0" />
      <span>El agente está procesando...</span>
    </div>
  )
}
