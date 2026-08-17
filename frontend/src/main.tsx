import {StrictMode, useEffect, useRef, useState} from 'react'
import {createRoot} from 'react-dom/client'
import type {components} from './api/schema'
import {
  API, ApiError, consumeSse, isChartDsl, newClientRequestId, requestJson,
  requestRecommendedQuestions, tokenExpiry,
} from './client'
import {AuthPage} from './auth/AuthPage'
import {AppShell} from './workbench/AppShell'
import {ChatComposer} from './workbench/ChatComposer'
import {ConversationStream} from './workbench/ConversationStream'
import {RunEvidenceRail} from './workbench/RunEvidenceRail'
import {SettingsPage} from './workbench/SettingsPage'
import {ThreadList} from './workbench/ThreadList'
import type {
  ArtifactRecord, ChartDsl, ChatResult, Identity, Interrupt, Message,
  ResultPage, StreamEvent, ThreadDetail, ThreadSummary, UserPreferences,
} from './types'
import './styles.css'

const PAGE_SIZE = 50
const DEFAULT_RECOMMENDED = [
  '昨天各品类的 GMV 是多少？',
  '昨天销售额是多少？',
  '昨天有多少已支付订单？',
  '昨天每个店铺的支付买家数？',
  '上周退款总金额是多少？',
  'orders 表有哪些字段？',
  '昨天哪几个品类的退款最多？',
  '最近 7 天日均 GMV？',
  '各品类订单占比？',
  'products 表有哪些字段？',
]
const HIDDEN_ANSWER_CODES = new Set(['PERMISSION_DENIED', 'REJECTED'])

function Workbench({token, logout}: {token: string; logout: () => void}) {
  const [identity, setIdentity] = useState<Identity>()
  const [threads, setThreads] = useState<ThreadSummary[]>([])
  const [messages, setMessages] = useState<Message[]>([])
  const [events, setEvents] = useState<StreamEvent[]>([])
  const [result, setResult] = useState<ResultPage>()
  const [resultId, setResultId] = useState<string>()
  const [chartDsl, setChartDsl] = useState<ChartDsl>()
  const [busy, setBusy] = useState(false)
  const [threadId, setThreadId] = useState<string>()
  const [requestId, setRequestId] = useState<string>()
  const [stateVersion, setStateVersion] = useState<number>()
  const [interrupt, setInterrupt] = useState<Interrupt>()
  const [error, setError] = useState<string>()
  const [errorCode, setErrorCode] = useState<string>()
  const [traceId, setTraceId] = useState<string>()
  const [connection, setConnection] = useState<'ready' | 'running' | 'reconnecting' | 'offline'>('ready')
  const [preferences, setPreferences] = useState<UserPreferences>({values: {}, schema_version: 'user_preferences_v1'})
  const [recommended, setRecommended] = useState<string[]>(DEFAULT_RECOMMENDED)
  const [threadTitles, setThreadTitles] = useState<Record<string, string>>({})
  const [route, setRoute] = useState<string>(() => location.pathname)
  const [threadDrawerOpen, setThreadDrawerOpen] = useState(false)
  const [evidenceDrawerOpen, setEvidenceDrawerOpen] = useState(false)
  const streamAbort = useRef<AbortController | null>(null)
  const resumeKeys = useRef(new Map<string, string>())
  const showSettings = route === '/app/settings'
  const resultRoute = route.match(/^\/app\/results\/([^/]+)$/)

  function navigate(path: string, replace = false) {
    if (replace) history.replaceState({}, '', path)
    else history.pushState({}, '', path)
    setRoute(path)
  }

  async function refreshThreads() {
    const value = await requestJson<components['schemas']['ThreadListResponse']>('/api/threads', token)
    const items = value.items ?? []
    setThreads(items)
    setThreadTitles(prev => {
      const next = {...prev}
      for (const item of items) if (!next[item.thread_id]) next[item.thread_id] = item.title
      return next
    })
  }

  async function loadResult(id: string, offset = 0) {
    const value = await requestJson<ResultPage>(`/api/results/${id}?offset=${offset}&limit=${PAGE_SIZE}`, token)
    setResult(value)
    setResultId(id)
  }

  async function loadArtifacts(ids: string[]) {
    setChartDsl(undefined)
    for (const id of ids) {
      try {
        const record = await requestJson<ArtifactRecord>(`/api/artifacts/${id}`, token)
        if (record.spec.type === 'CHART_DSL' && isChartDsl(record.payload)) setChartDsl(record.payload)
      } catch (value) {
        setError(value instanceof Error ? value.message : '结果制品无法读取')
      }
    }
  }

  async function openThread(id: string, push = true) {
    try {
      setError(undefined)
      setErrorCode(undefined)
      const detail = await requestJson<ThreadDetail>(`/api/threads/${id}`, token)
      const resultIds = detail.result_ids ?? []
      setThreadId(id)
      setMessages((detail.messages ?? []) as Message[])
      setStateVersion(detail.state_version ?? undefined)
      setInterrupt(detail.interrupt ?? undefined)
      setEvents([])
      if (resultIds.at(-1)) await loadResult(resultIds.at(-1)!)
      else {
        setResult(undefined)
        setResultId(undefined)
      }
      await loadArtifacts(detail.artifact_ids ?? [])
      if (push) navigate(`/app/threads/${id}`)
      setThreadDrawerOpen(false)
    } catch (value) {
      setError(value instanceof Error ? value.message : '线程无法读取')
    }
  }

  useEffect(() => {
    let active = true
    void (async () => {
      try {
        const [me, settings, questions] = await Promise.all([
          requestJson<Identity>('/api/me', token),
          requestJson<UserPreferences>('/api/settings', token),
          requestRecommendedQuestions().catch(() => DEFAULT_RECOMMENDED),
        ])
        if (!active) return
        setIdentity(me)
        setPreferences(settings)
        setRecommended(questions)
        await refreshThreads()
        const thread = location.pathname.match(/^\/app\/threads\/([^/]+)$/)
        const resultMatch = location.pathname.match(/^\/app\/results\/([^/]+)$/)
        if (thread) await openThread(decodeURIComponent(thread[1]), false)
        else if (resultMatch) await loadResult(decodeURIComponent(resultMatch[1]))
      } catch (value) {
        if (active) {
          setError(value instanceof Error ? value.message : '工作台初始化失败')
          if (value instanceof ApiError && value.status === 401) logout()
        }
      }
    })()
    const pop = () => {
      setRoute(location.pathname)
      const thread = location.pathname.match(/^\/app\/threads\/([^/]+)$/)
      const resultMatch = location.pathname.match(/^\/app\/results\/([^/]+)$/)
      if (thread) void openThread(decodeURIComponent(thread[1]), false)
      else if (resultMatch) void loadResult(decodeURIComponent(resultMatch[1]))
      else {
        setThreadId(undefined)
        setMessages([])
        setEvents([])
        setResult(undefined)
        setResultId(undefined)
        setChartDsl(undefined)
        setInterrupt(undefined)
      }
    }
    addEventListener('popstate', pop)
    return () => {
      active = false
      removeEventListener('popstate', pop)
    }
  }, [token])

  function rememberEvent(event: StreamEvent) {
    setEvents(old => event.eventId && old.some(item => item.eventId === event.eventId) ? old : [...old, event])
  }

  async function handleRunEvent(event: StreamEvent) {
    rememberEvent(event)
    if (event.event === 'thread.title_updated' && event.thread_id && event.thread_title) {
      setThreadTitles(prev => ({...prev, [event.thread_id!]: event.thread_title!}))
      setThreads(old => old.map(item => item.thread_id === event.thread_id ? {...item, title: event.thread_title!} : item))
      return
    }
    if (event.event === 'run.started') {
      setThreadId(event.thread_id)
      navigate(`/app/threads/${event.thread_id}`, true)
    }
    if (event.event === 'interrupt.created') {
      setThreadId(event.thread_id)
      setStateVersion(event.state_version ?? undefined)
      setInterrupt(event.interrupt ?? undefined)
      navigate(`/app/threads/${event.thread_id}`, true)
      await refreshThreads()
    }
    if (event.event === 'run.completed') {
      const ids = event.result_ids ?? []
      setThreadId(event.thread_id)
      setStateVersion(event.state_version ?? undefined)
      setInterrupt(event.interrupt ?? undefined)
      setMessages(old => [...old, {role: 'assistant', content: event.answer ?? '运行结束'}])
      if (ids[0]) await loadResult(ids[0])
      await loadArtifacts(event.artifact_ids ?? [])
      navigate(`/app/threads/${event.thread_id}`, true)
      await refreshThreads()
    }
    if (event.event === 'run.failed') {
      const code = event.error_code ?? 'REQUEST_FAILED'
      setThreadId(event.thread_id)
      setStateVersion(event.state_version ?? undefined)
      setErrorCode(code)
      if (event.answer && !HIDDEN_ANSWER_CODES.has(code)) {
        setMessages(old => [...old, {role: 'assistant', content: event.answer!}])
      }
      setError(new ApiError(code).message)
      navigate(`/app/threads/${event.thread_id}`, true)
    }
  }

  async function ask(textArg: string) {
    const text = textArg.trim()
    if (!text || busy) return
    const id = newClientRequestId()
    const controller = new AbortController()
    const cursor = {lastEventId: 0}
    streamAbort.current = controller
    setMessages(old => [...old, {role: 'user', content: text}])
    setRequestId(id)
    setBusy(true)
    setConnection('running')
    setError(undefined)
    setErrorCode(undefined)
    setTraceId(undefined)
    setResult(undefined)
    setResultId(undefined)
    setChartDsl(undefined)
    setEvents([])
    setInterrupt(undefined)
    const body: Record<string, unknown> = {
      message: text,
      timezone: String(preferences.values?.timezone ?? 'Asia/Shanghai'),
      request_id: id,
    }
    if (threadId) {
      body.thread_id = threadId
      if (stateVersion !== undefined) body.expected_state_version = stateVersion
    }
    try {
      for (let attempt = 0; attempt < 3; attempt++) {
        try {
          const reconnect = attempt > 0
          await consumeSse({
            url: reconnect ? `${API}/api/chat/stream?request_id=${encodeURIComponent(id)}` : `${API}/api/chat/stream`,
            method: reconnect ? 'GET' : 'POST',
            body: reconnect ? undefined : body,
            token,
            requestId: id,
            cursor,
            signal: controller.signal,
            onEvent: handleRunEvent,
          })
          setConnection('ready')
          break
        } catch (value) {
          if (controller.signal.aborted) throw value
          if (!(value instanceof TypeError) || attempt === 2) throw value
          setConnection('reconnecting')
        }
      }
    } catch (value) {
      if (controller.signal.aborted) {
        setConnection('ready')
        setError('已停止接收进度。服务端可能仍在执行，可从侧栏重新打开。')
      } else {
        setConnection('offline')
        setError(value instanceof Error ? value.message : '连接已中断')
      }
    } finally {
      if (streamAbort.current === controller) streamAbort.current = null
      setBusy(false)
    }
  }

  async function resume(answer: string) {
    if (!interrupt || !threadId || busy) return
    const key = resumeKeys.current.get(interrupt.interrupt_id) ?? newClientRequestId()
    resumeKeys.current.set(interrupt.interrupt_id, key)
    setBusy(true)
    setError(undefined)
    try {
      if (stateVersion === undefined) throw new Error('缺少会话版本，请重新打开会话。')
      const payload = await requestJson<ChatResult>(
        `/api/threads/${threadId}/interrupts/${interrupt.interrupt_id}/resume`,
        token,
        {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({answer, client_request_id: key, expected_state_version: stateVersion}),
        },
      )
      const ids = payload.result_ids ?? []
      resumeKeys.current.delete(interrupt.interrupt_id)
      setStateVersion(payload.state_version ?? undefined)
      setInterrupt(payload.interrupt ?? undefined)
      setTraceId(payload.trace_id ?? undefined)
      setMessages(old => [...old, {role: 'user', content: answer}, {role: 'assistant', content: payload.answer ?? '运行结束'}])
      if (ids[0]) await loadResult(ids[0])
      await loadArtifacts(payload.artifact_ids ?? [])
      await refreshThreads()
    } catch (value) {
      setError(value instanceof Error ? value.message : '恢复失败')
    } finally {
      setBusy(false)
    }
  }

  async function downloadCsv() {
    if (!resultId) return
    const headers = new Headers({
      Authorization: `Bearer ${token}`,
      'X-Request-ID': newClientRequestId(),
    })
    const response = await fetch(`${API}/api/results/${resultId}/export.csv`, {headers})
    if (!response.ok) {
      setError('CSV 导出失败，请确认结果仍在有效期内。')
      return
    }
    const url = URL.createObjectURL(await response.blob())
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `${resultId}.csv`
    anchor.click()
    URL.revokeObjectURL(url)
  }

  function newThread() {
    setThreadId(undefined)
    setMessages([])
    setEvents([])
    setResult(undefined)
    setResultId(undefined)
    setChartDsl(undefined)
    setInterrupt(undefined)
    setError(undefined)
    setErrorCode(undefined)
    setThreadDrawerOpen(false)
    navigate('/app')
  }

  async function saveTimezone(value: string) {
    try {
      setPreferences(await requestJson<UserPreferences>('/api/settings', token, {
        method: 'PUT',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({key: 'timezone', value, confirmed: true}),
      }))
      setError(undefined)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '偏好保存失败')
    }
  }

  const currentTitle = threadId ? threadTitles[threadId] : undefined
  const liveMessage = events.some(event => event.event === 'run.started') && !events.some(event => event.event === 'node.started')
    ? '正在理解问题'
    : undefined

  if (showSettings) {
    return (
      <SettingsPage
        timezone={String(preferences.values?.timezone ?? 'Asia/Shanghai')}
        error={error}
        onSave={value => void saveTimezone(value)}
        onBack={() => navigate('/app')}
      />
    )
  }

  return (
    <AppShell
      identity={identity}
      connection={connection}
      threadTitle={currentTitle}
      threadDrawerOpen={threadDrawerOpen}
      evidenceDrawerOpen={evidenceDrawerOpen}
      onToggleThreads={() => setThreadDrawerOpen(open => !open)}
      onToggleEvidence={() => setEvidenceDrawerOpen(open => !open)}
      onCloseDrawers={() => {
        setThreadDrawerOpen(false)
        setEvidenceDrawerOpen(false)
      }}
      onSettings={() => navigate('/app/settings')}
      onLogout={logout}
    >
      <ThreadList
        threads={threads}
        current={threadId}
        onOpen={id => void openThread(id)}
        onNew={newThread}
      />
      <ConversationStream
        messages={resultRoute ? [] : messages}
        interrupt={interrupt}
        chartDsl={chartDsl}
        result={result}
        busy={busy}
        connection={connection}
        error={error}
        recommended={recommended}
        onPick={text => void ask(text)}
        onResume={answer => void resume(answer)}
        onDownload={() => void downloadCsv()}
        onPage={offset => resultId && void loadResult(resultId, offset)}
        onReconnectThread={threadId ? () => void openThread(threadId) : undefined}
        traceId={traceId}
        requestId={requestId}
        errorCode={errorCode}
      />
      <RunEvidenceRail events={events} liveMessage={liveMessage} />
      <ChatComposer
        busy={busy}
        timezone={String(preferences.values?.timezone ?? 'Asia/Shanghai')}
        empty={messages.length === 0}
        onSend={text => void ask(text)}
        onStop={() => streamAbort.current?.abort()}
      />
    </AppShell>
  )
}

export function Root() {
  const [token, setToken] = useState<string>()
  const expiry = tokenExpiry(token as string)
  useEffect(() => {
    if (token && expiry && expiry.getTime() <= Date.now()) setToken(undefined)
  }, [token, expiry])
  useEffect(() => {
    if (!token && location.pathname !== '/login') history.replaceState({}, '', '/login')
    if (token && (location.pathname === '/' || location.pathname === '/login')) history.replaceState({}, '', '/app')
  }, [token])
  return token
    ? <Workbench token={token} logout={() => setToken(undefined)} />
    : <AuthPage onAuthenticated={value => setToken(value)} />
}

const rootElement = document.getElementById('root')
if (rootElement) createRoot(rootElement).render(<StrictMode><Root /></StrictMode>)
