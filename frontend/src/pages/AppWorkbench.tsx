import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiFetch } from '../api/client'
import { streamChat } from '../api/chat'
import {
  createSession,
  deleteSession,
  listSessions,
  listSessionTurns,
  type SessionSummary,
  type SessionTurn,
} from '../api/sessions'
import { useAuth } from '../auth/AuthContext'
import TurnCard, { type TurnView } from '../components/TurnCard'

interface ExampleItem {
  id: string
  question: string
}

type InitialData = {
  sessions: SessionSummary[]
  currentSessionId: string
  turns: TurnView[]
}

export default function AppWorkbench() {
  const navigate = useNavigate()
  const { user, logout } = useAuth()

  const [examples, setExamples] = useState<ExampleItem[]>([])
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null)
  const [turns, setTurns] = useState<TurnView[]>([])
  const [question, setQuestion] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [loading, setLoading] = useState(true)
  const [sideError, setSideError] = useState<string | null>(null)

  const abortRef = useRef<AbortController | null>(null)
  const activeRunRef = useRef<string | null>(null)
  const loadSequenceRef = useRef(0)
  const sessionLoadingRef = useRef(true)
  const turnIdRef = useRef(0)
  const initialLoadRef = useRef<Promise<InitialData> | null>(null)
  const examplesLoadRef = useRef<Promise<ExampleItem[]> | null>(null)
  const sessionsRef = useRef<SessionSummary[]>([])
  const currentSessionIdRef = useRef<string | null>(null)
  const deletingRef = useRef(false)

  useEffect(() => {
    sessionsRef.current = sessions
  }, [sessions])

  useEffect(() => {
    currentSessionIdRef.current = currentSessionId
  }, [currentSessionId])

  useEffect(() => {
    let cancelled = false
    const sequence = loadSequenceRef.current + 1
    loadSequenceRef.current = sequence
    setSessionLoading(true)

    if (!initialLoadRef.current) {
      initialLoadRef.current = loadInitialData()
    }

    initialLoadRef.current
      .then((data) => {
        if (cancelled || loadSequenceRef.current !== sequence) return
        updateSessions(data.sessions)
        updateCurrentSessionId(data.currentSessionId)
        setTurns(data.turns)
      })
      .catch((error) => {
        if (!cancelled && loadSequenceRef.current === sequence) {
          setSideError(
            error instanceof Error ? error.message : '工作台加载失败',
          )
        }
      })
      .finally(() => {
        if (!cancelled && loadSequenceRef.current === sequence) {
          setSessionLoading(false)
        }
      })

    return () => {
      cancelled = true
      abortRef.current?.abort()
    }
  }, [])

  useEffect(() => {
    let cancelled = false

    if (!examplesLoadRef.current) {
      examplesLoadRef.current = loadExamples()
    }

    examplesLoadRef.current
      .then((items) => {
        if (!cancelled) setExamples(items)
      })
      .catch(() => {
        if (!cancelled) setExamples([])
      })

    return () => {
      cancelled = true
    }
  }, [])

  function setSessionLoading(value: boolean) {
    sessionLoadingRef.current = value
    setLoading(value)
  }

  function updateSessions(
    updater:
      | SessionSummary[]
      | ((previous: SessionSummary[]) => SessionSummary[]),
  ) {
    const next =
      typeof updater === 'function'
        ? updater(sessionsRef.current)
        : updater
    sessionsRef.current = next
    setSessions(next)
  }

  function updateCurrentSessionId(sessionId: string | null) {
    currentSessionIdRef.current = sessionId
    setCurrentSessionId(sessionId)
  }

  function stopActiveStream() {
    abortRef.current?.abort()
    abortRef.current = null
    activeRunRef.current = null
    setStreaming(false)
    setTurns((previous) =>
      previous.map((turn) =>
        turn.streaming ? { ...turn, streaming: false } : turn,
      ),
    )
  }

  async function handleNewSession() {
    if (deletingRef.current) return

    stopActiveStream()
    const sequence = loadSequenceRef.current + 1
    loadSequenceRef.current = sequence
    setSessionLoading(true)
    setSideError(null)
    try {
      const session = await createSession()
      if (loadSequenceRef.current !== sequence) return
      updateSessions((previous) => [
        session,
        ...previous.filter((item) => item.id !== session.id),
      ])
      updateCurrentSessionId(session.id)
      setTurns([])
    } catch (error) {
      if (loadSequenceRef.current === sequence) {
        setSideError(
          error instanceof Error ? error.message : '创建会话失败',
        )
      }
    } finally {
      if (loadSequenceRef.current === sequence) setSessionLoading(false)
    }
  }

  async function handleSwitchSession(sessionId: string) {
    if (deletingRef.current) return
    if (sessionId === currentSessionIdRef.current) return

    stopActiveStream()
    const sequence = loadSequenceRef.current + 1
    loadSequenceRef.current = sequence
    updateCurrentSessionId(sessionId)
    setTurns([])
    setSessionLoading(true)
    setSideError(null)

    try {
      const history = await listSessionTurns(sessionId)
      if (
        loadSequenceRef.current !== sequence ||
        activeRunRef.current !== null
      ) {
        return
      }
      setTurns(mapHistoryTurns(sessionId, history))
    } catch (error) {
      if (loadSequenceRef.current === sequence) {
        setSideError(
          error instanceof Error ? error.message : '加载会话轮次失败',
        )
      }
    } finally {
      if (loadSequenceRef.current === sequence) setSessionLoading(false)
    }
  }

  async function handleDeleteSession(sessionId: string) {
    if (deletingRef.current) return
    if (!window.confirm('确定删除该会话？删除后不可恢复。')) return

    deletingRef.current = true
    let operationSequence: number | null = null
    setSideError(null)
    try {
      if (sessionId === currentSessionIdRef.current) stopActiveStream()
      await deleteSession(sessionId)
      const remainingSessions = sessionsRef.current.filter(
        (session) => session.id !== sessionId,
      )
      updateSessions(remainingSessions)

      if (sessionId !== currentSessionIdRef.current) return
      if (remainingSessions.length > 0) {
        stopActiveStream()
        const fallbackSessionId = remainingSessions[0].id
        const sequence = loadSequenceRef.current + 1
        operationSequence = sequence
        loadSequenceRef.current = sequence
        updateCurrentSessionId(fallbackSessionId)
        setTurns([])
        setSessionLoading(true)
        const history = await listSessionTurns(fallbackSessionId)
        if (
          loadSequenceRef.current === sequence &&
          activeRunRef.current === null
        ) {
          setTurns(mapHistoryTurns(fallbackSessionId, history))
        }
        return
      }

      stopActiveStream()
      operationSequence = loadSequenceRef.current + 1
      loadSequenceRef.current = operationSequence
      updateCurrentSessionId(null)
      setTurns([])
      setSessionLoading(true)
      const session = await createSession()
      if (loadSequenceRef.current !== operationSequence) return
      updateSessions([session])
      updateCurrentSessionId(session.id)
    } catch (error) {
      setSideError(
        error instanceof Error ? error.message : '删除会话失败',
      )
    } finally {
      if (
        operationSequence !== null &&
        loadSequenceRef.current === operationSequence
      ) {
        setSessionLoading(false)
      }
      deletingRef.current = false
    }
  }

  function handleLogout() {
    stopActiveStream()
    logout()
    navigate('/')
  }

  function toggleTurnSection(
    turnId: string,
    section: keyof TurnView['open'],
  ) {
    updateTurn(turnId, (turn) => ({
      ...turn,
      open: { ...turn.open, [section]: !turn.open[section] },
    }))
  }

  function updateTurn(
    turnId: string,
    updater: (turn: TurnView) => TurnView,
  ) {
    setTurns((previous) =>
      previous.map((turn) =>
        turn.id === turnId ? updater(turn) : turn,
      ),
    )
  }

  function pushTrace(turnId: string, event: string, summary: string) {
    updateTurn(turnId, (turn) => ({
      ...turn,
      trace: [
        ...turn.trace,
        {
          id: (turn.trace[turn.trace.length - 1]?.id ?? 0) + 1,
          event,
          summary,
        },
      ],
    }))
  }

  async function handleSubmit(event?: FormEvent) {
    event?.preventDefault()
    const submittedQuestion = question.trim()
    if (
      !submittedQuestion ||
      streaming ||
      activeRunRef.current !== null ||
      sessionLoadingRef.current ||
      !user ||
      !currentSessionId
    ) {
      return
    }

    loadSequenceRef.current += 1
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller
    turnIdRef.current += 1
    const turnId = `${currentSessionId}-live-${turnIdRef.current}`
    let runTraceId: string | null = null

    activeRunRef.current = turnId
    setStreaming(true)
    setQuestion('')
    setTurns((previous) => [
      ...previous,
      createEmptyTurn(turnId, submittedQuestion),
    ])

    try {
      await streamChat({
        question: submittedQuestion,
        sessionId: currentSessionId,
        signal: controller.signal,
        onEvent: (sseEvent, data) => {
          switch (sseEvent) {
            case 'run_start':
              runTraceId = data.trace_id
                ? String(data.trace_id)
                : data.request_id
                  ? String(data.request_id)
                  : null
              pushTrace(
                turnId,
                sseEvent,
                `trace ${String(data.trace_id ?? data.request_id ?? '')}`.trim(),
              )
              break
            case 'node_start':
              pushTrace(
                turnId,
                sseEvent,
                `开始 ${String(data.node ?? '')}`,
              )
              break
            case 'node_end': {
              const node = String(data.node ?? '')
              const summary = String(data.summary ?? '完成')
              if (node === 'SQLGuardrail' && summary !== 'rejected') {
                updateTurn(turnId, (turn) => ({
                  ...turn,
                  guardrailPassed: true,
                }))
              }
              pushTrace(turnId, sseEvent, `${node}: ${summary}`)
              break
            }
            case 'sql':
              updateTurn(turnId, (turn) => ({
                ...turn,
                sql: String(data.sql ?? ''),
                sqlRepaired: Boolean(data.repaired),
              }))
              pushTrace(turnId, sseEvent, '收到 SQL')
              break
            case 'rows': {
              const columns = Array.isArray(data.columns)
                ? (data.columns as string[])
                : []
              const rows = Array.isArray(data.rows)
                ? (data.rows as Record<string, unknown>[])
                : []
              updateTurn(turnId, (turn) => ({
                ...turn,
                columns,
                rows,
                guardrailPassed: true,
                open: { ...turn.open, rows: true },
              }))
              pushTrace(turnId, sseEvent, `${rows.length} 行`)
              break
            }
            case 'answer':
              updateTurn(turnId, (turn) => ({
                ...turn,
                answer: String(data.text ?? ''),
              }))
              pushTrace(turnId, sseEvent, '结论已生成')
              break
            case 'tool_start':
              pushTrace(
                turnId,
                sseEvent,
                `调用 ${String(data.tool ?? '')}`.trim(),
              )
              break
            case 'tool_end': {
              const riskPrefix =
                data.risk_level === 'high' ? '⚠ high · ' : ''
              pushTrace(
                turnId,
                sseEvent,
                `${riskPrefix}${String(data.tool ?? '')}: ${String(data.status ?? 'done')}`,
              )
              break
            }
            case 'chart':
              updateTurn(turnId, (turn) => ({
                ...turn,
                chart: {
                  type: String(data.type ?? 'table'),
                  x: String(data.x ?? ''),
                  y: String(data.y ?? ''),
                  title: data.title ? String(data.title) : undefined,
                  series: Array.isArray(data.series)
                    ? data.series.map(String)
                    : undefined,
                },
                open: { ...turn.open, chart: true },
              }))
              pushTrace(turnId, sseEvent, String(data.type ?? 'chart'))
              break
            case 'write_result':
              updateTurn(turnId, (turn) => ({
                ...turn,
                writeResult: {
                  affected_rows:
                    typeof data.affected_rows === 'number'
                      ? data.affected_rows
                      : null,
                  sql: String(data.sql ?? ''),
                },
              }))
              pushTrace(
                turnId,
                sseEvent,
                `写操作 · ${
                  typeof data.affected_rows === 'number'
                    ? data.affected_rows
                    : '?'
                } 行`,
              )
              break
            case 'route_decision':
              pushTrace(
                turnId,
                sseEvent,
                `${String(data.route_mode ?? '')} · ${String(data.route_source ?? '')}`,
              )
              break
            case 'session_title': {
              const title = String(data.title ?? '').slice(0, 10)
              const sessionId = String(
                data.session_id ??
                  currentSessionIdRef.current ??
                  '',
              )
              if (!title || !sessionId) break
              updateSessions((previous) =>
                previous.map((session) =>
                  session.id === sessionId
                    ? { ...session, title }
                    : session,
                ),
              )
              break
            }
            case 'error': {
              const traceId =
                (data.trace_id ? String(data.trace_id) : null) ||
                (data.request_id ? String(data.request_id) : null) ||
                runTraceId
              updateTurn(turnId, (turn) => ({
                ...turn,
                error: String(data.message ?? '分析失败'),
                errorTraceId: traceId,
              }))
              pushTrace(
                turnId,
                sseEvent,
                traceId
                  ? `${String(data.message ?? 'error')} · ${traceId}`
                  : String(data.message ?? 'error'),
              )
              break
            }
            case 'done':
              updateTurn(turnId, (turn) => ({
                ...turn,
                latencyMs:
                  typeof data.latency_ms === 'number'
                    ? data.latency_ms
                    : turn.latencyMs,
                error: data.need_clarification === true ? null : turn.error,
                clarificationHint:
                  data.need_clarification === true
                    ? '需要补充信息后继续'
                    : turn.clarificationHint,
              }))
              pushTrace(
                turnId,
                sseEvent,
                `完成 ${data.latency_ms ?? ''}ms`,
              )
              break
            default:
              pushTrace(
                turnId,
                sseEvent,
                JSON.stringify(data).slice(0, 80),
              )
          }
        },
      })
      // Sync titles/turn_count from server (SSE session_title may be missed)
      try {
        const listed = await listSessions()
        updateSessions(listed)
      } catch {
        const sid = currentSessionIdRef.current
        updateSessions((previous) => {
          const session = previous.find((item) => item.id === sid)
          if (!session || !sid) return previous
          const updated = {
            ...session,
            updated_at: new Date().toISOString(),
            turn_count: session.turn_count + 1,
          }
          return [
            updated,
            ...previous.filter((item) => item.id !== sid),
          ]
        })
      }
    } catch (error) {
      if ((error as Error).name !== 'AbortError') {
        updateTurn(turnId, (turn) => ({
          ...turn,
          error: error instanceof Error ? error.message : '请求失败',
        }))
      }
    } finally {
      updateTurn(turnId, (turn) => ({ ...turn, streaming: false }))
      if (activeRunRef.current === turnId) {
        activeRunRef.current = null
        abortRef.current = null
        setStreaming(false)
      }
    }
  }

  const currentSession = sessions.find(
    (session) => session.id === currentSessionId,
  )
  const currentTitle = currentSession?.title || '新会话'

  return (
    <div className="flex h-screen overflow-hidden bg-bg text-ink">
      <aside className="flex w-72 shrink-0 flex-col border-r border-line bg-surface">
        <div className="border-b border-line px-5 py-5">
          <p className="font-display text-xl leading-tight tracking-tight">
            data-analysis-agent
          </p>
          <p className="mt-1 text-xs text-muted">企业经营数据分析 Agent</p>
          <div className="mt-4 rounded-lg bg-accent-soft px-3 py-2 text-sm">
            <p className="font-medium text-accent">{user?.username}</p>
            <span className="mt-1 inline-flex rounded-md bg-surface px-2 py-0.5 text-[11px] font-medium uppercase tracking-wide text-accent">
              {user?.role}
            </span>
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-4 py-4">
          {sideError && (
            <p className="mb-3 text-xs text-red-700">{sideError}</p>
          )}

          <section>
            <div className="flex items-center justify-between gap-2">
              <h2 className="text-[11px] font-medium uppercase tracking-wider text-muted">
                会话
              </h2>
              <button
                type="button"
                onClick={handleNewSession}
                disabled={loading}
                className="rounded-md bg-accent-soft px-2 py-1 text-xs font-medium text-accent transition-colors hover:bg-accent hover:text-white disabled:opacity-40"
              >
                + 新建
              </button>
            </div>
            <ul className="mt-2 space-y-1">
              {sessions.map((session) => {
                const active = session.id === currentSessionId
                return (
                  <li key={session.id} className="flex items-center gap-1">
                    <button
                      type="button"
                      onClick={() => handleSwitchSession(session.id)}
                      className={`min-w-0 flex-1 rounded-lg px-3 py-2 text-left transition-colors ${
                        active
                          ? 'bg-accent-soft text-accent'
                          : 'text-ink hover:bg-bg'
                      }`}
                    >
                      <span className="block truncate text-xs font-medium">
                        {session.title || '新会话'}
                      </span>
                      <span className="mt-1 block text-[10px] text-muted">
                        {formatSessionTime(session.updated_at)}
                        {session.turn_count > 0
                          ? ` · ${session.turn_count} 轮`
                          : ''}
                      </span>
                    </button>
                    <button
                      type="button"
                      aria-label="删除会话"
                      onClick={(event) => {
                        event.stopPropagation()
                        void handleDeleteSession(session.id)
                      }}
                      className="shrink-0 rounded-md px-2 py-2 text-xs text-muted transition-colors hover:bg-accent-soft hover:text-accent"
                    >
                      删除
                    </button>
                  </li>
                )
              })}
            </ul>
          </section>
        </div>

        <div className="space-y-2 border-t border-line p-4">
          <button
            type="button"
            onClick={() => navigate('/app/tables')}
            className="w-full rounded-lg bg-accent-soft px-3 py-2 text-sm font-medium text-accent transition-colors hover:bg-accent hover:text-white"
          >
            查看全部数据表
          </button>
          <button
            type="button"
            onClick={handleLogout}
            className="w-full rounded-lg border border-line px-3 py-2 text-sm text-muted transition-colors hover:border-accent hover:text-accent"
          >
            退出登录
          </button>
        </div>
      </aside>

      <main className="flex min-w-0 flex-1 flex-col">
        <header className="border-b border-line bg-surface px-6 py-4">
          <h1 className="truncate font-display text-lg">{currentTitle}</h1>
          <p className="text-xs text-muted">
            多轮经营分析 · SQL 经 Guardrail 后执行
          </p>
        </header>

        <div className="flex min-h-0 flex-1 overflow-y-auto">
          <div
            className={`mx-auto w-full px-6 ${
              !loading && turns.length === 0
                ? 'max-w-6xl py-4'
                : 'max-w-5xl space-y-7 py-6'
            }`}
          >
            {loading && turns.length === 0 ? (
              <p className="py-16 text-center text-sm text-muted">
                正在加载会话…
              </p>
            ) : !loading && turns.length === 0 ? (
              <div className="flex flex-col py-1">
                <div className="shrink-0 text-center">
                  <p className="font-display text-xl tracking-tight">
                    从一个经营问题开始
                  </p>
                  <p className="mt-1.5 text-sm text-muted">
                    可从下方示例选择，或在底部直接输入问题。
                  </p>
                </div>
                <ul className="mt-5 grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
                  {examples.map((example, index) => (
                    <li
                      key={example.id}
                      className="min-w-0 animate-rise"
                      style={{
                        animationDelay: `${Math.min(index, 12) * 28}ms`,
                      }}
                    >
                      <button
                        type="button"
                        onClick={() => setQuestion(example.question)}
                        className="group flex h-full w-full items-start rounded-xl border border-line bg-surface px-3 py-2.5 text-left text-[13px] leading-snug text-ink shadow-[0_1px_0_rgba(18,20,26,0.04)] transition-[transform,border-color,background-color,box-shadow,color] duration-200 hover:-translate-y-0.5 hover:border-accent/35 hover:bg-accent-soft hover:text-accent hover:shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/30"
                      >
                        <span className="line-clamp-2">{example.question}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            ) : (
              turns.map((turn) => (
                <TurnCard
                  key={turn.id}
                  turn={turn}
                  onToggle={(section) =>
                    toggleTurnSection(turn.id, section)
                  }
                />
              ))
            )}
          </div>
        </div>

        <div className="border-t border-line bg-surface px-6 py-4">
          <form
            onSubmit={handleSubmit}
            className="mx-auto flex max-w-5xl items-end gap-3"
          >
            <textarea
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              rows={2}
              placeholder="继续追问，或输入新的分析问题…"
              className="min-h-[3.5rem] flex-1 resize-y rounded-xl border border-line bg-bg px-4 py-3 text-sm outline-none ring-accent/30 focus:ring-2"
              disabled={loading || streaming || !currentSessionId}
            />
            <button
              type="submit"
              disabled={
                loading ||
                streaming ||
                !question.trim() ||
                !currentSessionId
              }
              className="rounded-xl bg-accent px-5 py-3 text-sm font-medium text-white transition-opacity disabled:opacity-40"
            >
              {streaming ? '分析中…' : '分析'}
            </button>
          </form>
        </div>
      </main>
    </div>
  )
}

async function loadInitialData(): Promise<InitialData> {
  const listedSessions = await listSessions()
  const session =
    listedSessions.length > 0 ? listedSessions[0] : await createSession()
  const sessions =
    listedSessions.length > 0 ? listedSessions : [session]
  const history = await listSessionTurns(session.id)

  return {
    sessions,
    currentSessionId: session.id,
    turns: mapHistoryTurns(session.id, history),
  }
}

async function loadExamples(): Promise<ExampleItem[]> {
  const response = await apiFetch('/api/examples')
  if (!response.ok) throw new Error('加载示例失败')
  const data = await response.json()
  return data.examples ?? []
}

function mapHistoryTurns(
  sessionId: string,
  history: SessionTurn[],
): TurnView[] {
  return history.map((turn) => {
    const display = turn.display ?? null
    const columns = Array.isArray(display?.columns) ? display.columns : []
    const rows = Array.isArray(display?.rows) ? display.rows : []
    const chart =
      display?.chart &&
      typeof display.chart === 'object' &&
      display.chart.type &&
      display.chart.type !== 'table'
        ? {
            type: String(display.chart.type),
            x: String(display.chart.x ?? ''),
            y: String(display.chart.y ?? ''),
            title: display.chart.title
              ? String(display.chart.title)
              : undefined,
            series: Array.isArray(display.chart.series)
              ? display.chart.series.map(String)
              : undefined,
          }
        : null
    const trace = Array.isArray(display?.trace)
      ? display.trace.map((entry, index) => ({
          id: index + 1,
          event: String(entry.event ?? ''),
          summary: String(entry.summary ?? ''),
        }))
      : []
    const hasRows = columns.length > 0 && rows.length > 0
    const hasChart = chart != null && Boolean(chart.x) && Boolean(chart.y)
    return {
      id: `${sessionId}-history-${turn.turn_index}`,
      question: turn.question,
      answer: turn.result_summary ?? '',
      sql: turn.sql_text ?? '',
      sqlRepaired: Boolean(display?.sql_repaired),
      guardrailPassed: Boolean(
        display?.guardrail_passed || (turn.sql_text && hasRows),
      ),
      columns,
      rows,
      chart,
      writeResult: null,
      trace,
      error: null,
      errorTraceId: null,
      clarificationHint: null,
      latencyMs: null,
      streaming: false,
      fromHistory: true,
      open: {
        sql: Boolean(turn.sql_text),
        rows: hasRows,
        chart: hasChart,
        trace: false,
      },
    }
  })
}

function createEmptyTurn(id: string, question: string): TurnView {
  return {
    id,
    question,
    answer: '',
    sql: '',
    sqlRepaired: false,
    guardrailPassed: false,
    columns: [],
    rows: [],
    chart: null,
    writeResult: null,
    trace: [],
    error: null,
    errorTraceId: null,
    clarificationHint: null,
    latencyMs: null,
    streaming: true,
    fromHistory: false,
    open: { sql: true, rows: true, chart: true, trace: false },
  }
}

function formatSessionTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}
