import {useCallback, useEffect, useRef, useState} from 'react'
import {requestJson, readSse} from '../client'
import {roleLabel, type AppMeta, type ChatMessage, type ResultPage, type ThinkStep, type Thread, type UserInfo, type WritePreview} from '../types'
import {ChatComposer} from './ChatComposer'
import {ConversationStream} from './ConversationStream'
import {ThreadList} from './ThreadList'

let msgSeq = 0
function nextId(): string {
  msgSeq += 1
  return `m-${msgSeq}`
}

function settleInterrupts(messages: ChatMessage[]): ChatMessage[] {
  return messages.map(item =>
    item.interrupt && !item.interrupt.resolved
      ? {...item, interrupt: {...item.interrupt, resolved: true}}
      : item,
  )
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
  const inFlight = useRef(false)
  const [liveId, setLiveId] = useState<string | undefined>()
  const [meta, setMeta] = useState<AppMeta>({
    greeting: '',
    suggested_questions: [],
    empty_thread_title: '',
    role_labels: {},
  })

  const refreshThreads = useCallback(async () => {
    const data = await requestJson<{threads: Thread[]}>('/api/threads', token)
    setThreads(data.threads)
  }, [token])

  useEffect(() => {
    void refreshThreads()
    void requestJson<AppMeta>('/api/meta', token).then(setMeta)
  }, [refreshThreads, token])

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

  function toggleThink(id: string) {
    setMessages(prev =>
      prev.map(item => {
        if (item.id !== id) return item
        const open = item.thinkingOpen ?? item.id === liveId
        return {...item, thinkingOpen: !open}
      }),
    )
  }

  function patchAssistant(id: string, patch: (item: ChatMessage) => ChatMessage) {
    setMessages(prev => prev.map(item => (item.id === id ? patch(item) : item)))
  }

  async function send(text: string, threadId?: string) {
    if (inFlight.current) return
    inFlight.current = true
    const id = threadId ?? currentId ?? (await newThread())
    if (!currentId) setCurrentId(id)
    const userMsg: ChatMessage = {id: nextId(), role: 'user', text}
    const assistantId = nextId()
    setMessages(prev => [
      ...settleInterrupts(prev),
      userMsg,
      {id: assistantId, role: 'assistant', text: '', thinking: [], thinkingOpen: true},
    ])
    setLiveId(assistantId)
    setBusy(true)
    let answer = ''
    let interrupt: WritePreview | undefined
    let resultId: string | undefined
    try {
      await readSse(`/api/threads/${id}/messages`, token, {message: text}, (event, data) => {
        if (event === 'think') {
          const step: ThinkStep = {
            node: String(data.node ?? ''),
            label: String(data.label ?? ''),
            text: String(data.text ?? ''),
          }
          patchAssistant(assistantId, item => ({
            ...item,
            thinking: [...(item.thinking ?? []), step],
          }))
        }
        if (event === 'token') {
          answer += String(data.text ?? '')
          patchAssistant(assistantId, item => ({
            ...item,
            text: answer,
            thinkingOpen: false,
          }))
        }
        if (event === 'interrupt') {
          interrupt = data as WritePreview
          patchAssistant(assistantId, item => ({
            ...item,
            interrupt,
            thinkingOpen: false,
          }))
        }
        if (event === 'result_ref') resultId = String(data.result_id ?? '')
        if (event === 'error') {
          answer = String(data.message ?? '出错了')
          patchAssistant(assistantId, item => ({
            ...item,
            text: answer,
            thinkingOpen: false,
          }))
        }
      })
      let result: ResultPage | undefined
      if (resultId) {
        result = await requestJson<ResultPage>(`/api/results/${resultId}?offset=0&limit=20`, token)
      }
      setMessages(prev =>
        prev.map(item =>
          item.id === assistantId
            ? {...item, text: answer, interrupt, result, thinkingOpen: item.thinkingOpen ?? false}
            : item,
        ),
      )
      await refreshThreads()
      window.setTimeout(() => {
        void refreshThreads()
      }, 1500)
    } finally {
      inFlight.current = false
      setLiveId(undefined)
      setBusy(false)
    }
  }

  async function resume(payload: Record<string, unknown>) {
    if (!currentId || inFlight.current) return
    inFlight.current = true
    const assistantId = nextId()
    setMessages(prev => [
      ...settleInterrupts(prev),
      {id: assistantId, role: 'assistant', text: '', thinking: [], thinkingOpen: true},
    ])
    setLiveId(assistantId)
    setBusy(true)
    let answer = ''
    try {
      await readSse(`/api/threads/${currentId}/resume`, token, payload, (event, data) => {
        if (event === 'think') {
          const step: ThinkStep = {
            node: String(data.node ?? ''),
            label: String(data.label ?? ''),
            text: String(data.text ?? ''),
          }
          patchAssistant(assistantId, item => ({
            ...item,
            thinking: [...(item.thinking ?? []), step],
          }))
        }
        if (event === 'token') {
          answer += String(data.text ?? '')
          patchAssistant(assistantId, item => ({
            ...item,
            text: answer,
            thinkingOpen: false,
          }))
        }
        if (event === 'error') {
          answer = String(data.message ?? '出错了')
          patchAssistant(assistantId, item => ({
            ...item,
            text: answer,
            thinkingOpen: false,
          }))
        }
      })
      patchAssistant(assistantId, item => ({
        ...item,
        text: answer || '已处理',
        thinkingOpen: false,
      }))
    } finally {
      inFlight.current = false
      setLiveId(undefined)
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
              emptyTitle={meta.empty_thread_title}
              onSelect={id => {
                setCurrentId(id)
                setMessages([])
              }}
              onDelete={id => void removeThread(id)}
            />
            <div className="sidebar-user">
              <span>{user.display_name}</span>
              <span className="role-tag">{roleLabel(user.role, meta.role_labels)}</span>
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
            <h1>{meta.greeting}</h1>
            <div className="suggest">
              {meta.suggested_questions.map(question => (
                <button key={question} type="button" className="chip" onClick={() => void send(question)}>
                  {question}
                </button>
              ))}
            </div>
          </div>
        ) : (
            <ConversationStream
              messages={messages}
              role={user.role}
              liveId={liveId}
              onResume={payload => void resume(payload)}
              onPick={text => void send(text)}
              onToggleThink={toggleThink}
              suggestions={meta.suggested_questions}
              disabled={busy}
            />
        )}
        <ChatComposer disabled={busy} onSend={text => void send(text)} />
      </main>
    </div>
  )
}
