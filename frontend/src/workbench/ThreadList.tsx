import {useMemo, useState} from 'react'
import type {ThreadSummary} from '../types'

export function ThreadList({
  threads,
  current,
  onOpen,
  onNew,
  onDelete,
}: {
  threads: ThreadSummary[]
  current?: string
  onOpen: (id: string) => void
  onNew: () => void
  onDelete: (id: string) => void
}) {
  const [query, setQuery] = useState('')
  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) return threads
    return threads.filter(thread =>
      thread.title.toLowerCase().includes(needle) || thread.thread_id.toLowerCase().includes(needle))
  }, [query, threads])

  return (
    <aside className="thread-list" aria-label="会话列表">
      <button type="button" className="thread-new" onClick={onNew}>新建问题</button>
      <label className="thread-search">
        <span className="sr-only">搜索线程</span>
        <input
          value={query}
          onChange={event => setQuery(event.target.value)}
          placeholder="搜索线程"
          aria-label="搜索线程"
        />
      </label>
      <p className="thread-section">最近会话</p>
      <nav className="thread-nav">
        {visible.length === 0
          ? <p className="thread-empty">完成第一次分析后会出现在这里。</p>
          : visible.map(thread => {
              const active = thread.thread_id === current
              return (
                <div
                  key={thread.thread_id}
                  className={active ? 'thread-row active' : 'thread-row'}
                >
                  <button
                    type="button"
                    className="thread-item"
                    onClick={() => onOpen(thread.thread_id)}
                    aria-current={active ? 'page' : undefined}
                    aria-label={thread.title}
                  >
                    <span className="thread-item-title">{thread.title}</span>
                    <time className="thread-item-time">{formatRelative(thread.updated_at)}</time>
                  </button>
                  <button
                    type="button"
                    className="thread-delete"
                    aria-label={`删除 ${thread.title}`}
                    onClick={() => onDelete(thread.thread_id)}
                  >
                    删除
                  </button>
                </div>
              )
            })}
      </nav>
    </aside>
  )
}

function formatRelative(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const minutes = Math.round((Date.now() - date.getTime()) / 60_000)
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.round(hours / 24)
  if (days < 7) return `${days} 天前`
  return date.toLocaleDateString()
}
