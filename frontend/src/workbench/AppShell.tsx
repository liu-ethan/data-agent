import {useCallback, useEffect, useState} from 'react'
import {requestJson, readSse} from '../client'
import {SUGGESTED_QUESTIONS, roleLabel, type ChatMessage, type ResultPage, type Thread, type UserInfo, type WritePreview} from '../types'
import {ChatComposer} from './ChatComposer'
import {ConversationStream} from './ConversationStream'
import {ThreadList} from './ThreadList'

let msgSeq = 0
function nextId(): string {
  msgSeq += 1
  return `m-${msgSeq}`
}

export function AppShell({
  token,
  user,
  onLogout,
}: {
  token: string
  user: UserInfo
  onLogout: () => void
}) {
  const [collapsed, setCollapsed] = useState(false)
  const [threads, setThreads] = useState<Thread[]>([])
  const [currentId, setCurrentId] = useState<string | undefined>()
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [busy, setBusy] = useState(false)

  const refreshThreads = useCallback(async () => {
    const data = await requestJson<{threads: Thread[]}>('/api/threads', token)
    setThreads(data.threads)
  }, [token])

  useEffect(() => {
    void refreshThreads()
  }, [refreshThreads])

  async function newThread(): Promise<string> {
    const created = await requestJson<{thread_id: string; title: string}>('/api/threads', token, {
      method: 'POST',
      body: JSON.stringify({}),
    })
    setCurrentId(created.thread_id)
    setMessages([])
    await refreshThreads()
    return created.thread_id
  }

  async function send(text: string, threadId?: string) {
    const id = threadId ?? currentId ?? (await newThread())
    if (!currentId) setCurrentId(id)
    const userMsg: ChatMessage = {id: nextId(), role: 'user', text}
    const assistantId = nextId()
    setMessages(prev => [...prev, userMsg, {id: assistantId, role: 'assistant', text: ''}])
    setBusy(true)
    let answer = ''
    let interrupt: WritePreview | undefined
    let resultId: string | undefined
    try {
      await readSse(`/api/threads/${id}/messages`, token, {message: text}, (event, data) => {
        if (event === 'token') answer += String(data.text ?? '')
        if (event === 'interrupt') interrupt = data as WritePreview
        if (event === 'result_ref') resultId = String(data.result_id ?? '')
        if (event === 'error') answer = String(data.message ?? '出错了')
      })
      let result: ResultPage | undefined
      if (resultId) {
        result = await requestJson<ResultPage>(`/api/results/${resultId}?offset=0&limit=20`, token)
      }
      setMessages(prev =>
        prev.map(item =>
          item.id === assistantId
            ? {...item, text: answer, interrupt, result}
            : item,
        ),
      )
      await refreshThreads()
      window.setTimeout(() => {
        void refreshThreads()
      }, 1500)
    } finally {
      setBusy(false)
    }
  }

  async function resume(payload: Record<string, unknown>) {
    if (!currentId) return
    setBusy(true)
    let answer = ''
    try {
      await readSse(`/api/threads/${currentId}/resume`, token, payload, (event, data) => {
        if (event === 'token') answer += String(data.text ?? '')
      })
      setMessages(prev => [...prev, {id: nextId(), role: 'assistant', text: answer || '已处理'}])
    } finally {
      setBusy(false)
    }
  }

  async function removeThread(id: string) {
    await requestJson(`/api/threads/${id}`, token, {method: 'DELETE'})
    if (currentId === id) {
      setCurrentId(undefined)
      setMessages([])
    }
    await refreshThreads()
  }

  const empty = messages.length === 0

  return (
    <div className={`shell ${collapsed ? 'collapsed' : ''}`}>
      <aside className="sidebar">
        <div className="sidebar-top">
          <button type="button" className="icon-btn" aria-label="折叠侧栏" onClick={() => setCollapsed(v => !v)}>
            ☰
          </button>
          {collapsed ? null : <strong className="brand">问数</strong>}
        </div>
        {collapsed ? null : (
          <>
            <button type="button" className="new-thread" onClick={() => void newThread()}>
              新对话
            </button>
            <p className="recent-label">最近</p>
            <ThreadList
              threads={threads}
              currentId={currentId}
              onSelect={id => {
                setCurrentId(id)
                setMessages([])
              }}
              onDelete={id => void removeThread(id)}
            />
            <div className="sidebar-user">
              <span>{user.display_name}</span>
              <span className="role-tag">{roleLabel(user.role)}</span>
              <button type="button" onClick={onLogout}>
                退出
              </button>
            </div>
          </>
        )}
      </aside>
      <main className="panel">
        {empty ? (
          <div className="hero">
            <h1>想查哪一块经营数据？</h1>
            <div className="suggest">
              {SUGGESTED_QUESTIONS.map(question => (
                <button key={question} type="button" className="chip" onClick={() => void send(question)}>
                  {question}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <ConversationStream messages={messages} role={user.role} onResume={payload => void resume(payload)} />
        )}
        <ChatComposer disabled={busy} onSend={text => void send(text)} />
      </main>
    </div>
  )
}
