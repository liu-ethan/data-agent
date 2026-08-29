import type {Thread} from '../types'

const DOTS = ['#3B6CFF', '#34C759', '#FF9F0A', '#AF52DE', '#FF375F', '#64D2FF']

function colorFor(id: string): string {
  let hash = 0
  for (const ch of id) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0
  return DOTS[hash % DOTS.length]
}

export function ThreadList({
  threads,
  currentId,
  emptyTitle,
  onSelect,
  onDelete,
}: {
  threads: Thread[]
  currentId?: string
  emptyTitle: string
  onSelect: (id: string) => void
  onDelete: (id: string) => void
}) {
  if (!threads.length) {
    return <p className="thread-empty">还没有对话</p>
  }
  return (
    <ul className="thread-list">
      {threads.map(thread => (
        <li key={thread.thread_id} className={thread.thread_id === currentId ? 'current' : ''}>
          <button type="button" className="thread-open" onClick={() => onSelect(thread.thread_id)}>
            <span className="thread-dot" style={{background: colorFor(thread.thread_id)}} />
            <span className="thread-title">{thread.title || emptyTitle}</span>
          </button>
          <button
            type="button"
            className="thread-delete"
            aria-label={`删除 ${thread.title || emptyTitle}`}
            onClick={() => {
              if (window.confirm('删除这个对话？')) onDelete(thread.thread_id)
            }}
          >
            ×
          </button>
        </li>
      ))}
    </ul>
  )
}
