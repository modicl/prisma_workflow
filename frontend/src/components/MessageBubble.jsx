export default function MessageBubble({ role, content }) {
  const isAgent = role === 'agent'
  return (
    <div className={`flex ${isAgent ? 'justify-start' : 'justify-end'} mb-3`}>
      <div
        className={`max-w-[80%] rounded-2xl px-4 py-2.5 text-sm whitespace-pre-wrap leading-relaxed ${
          isAgent
            ? 'bg-blue-50 text-blue-900 rounded-tl-sm'
            : 'bg-gray-100 text-gray-700 rounded-tr-sm'
        }`}
      >
        {content}
      </div>
    </div>
  )
}
