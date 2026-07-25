import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { apiFetch } from '../api/client'
import { streamChat } from '../api/chat'
import { useAuth } from '../auth/AuthContext'
import ResultChart, { type ChartConfig } from '../components/ResultChart'

interface ExampleItem {
  id: string
  question: string
}

interface SchemaTable {
  name: string
  columns: { name: string; type: string; nullable: boolean }[]
}

interface TraceEntry {
  id: number
  event: string
  summary: string
}

export default function AppWorkbench() {
  const navigate = useNavigate()
  const { user, logout } = useAuth()

  const [examples, setExamples] = useState<ExampleItem[]>([])
  const [tables, setTables] = useState<SchemaTable[]>([])
  const [sideError, setSideError] = useState<string | null>(null)

  const [question, setQuestion] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [answer, setAnswer] = useState('')
  const [sql, setSql] = useState('')
  const [sqlRepaired, setSqlRepaired] = useState(false)
  const [columns, setColumns] = useState<string[]>([])
  const [rows, setRows] = useState<Record<string, unknown>[]>([])
  const [error, setError] = useState<string | null>(null)
  const [trace, setTrace] = useState<TraceEntry[]>([])
  const [traceOpen, setTraceOpen] = useState(false)
  const [latencyMs, setLatencyMs] = useState<number | null>(null)
  const [guardrailPassed, setGuardrailPassed] = useState(false)
  const [clarificationHint, setClarificationHint] = useState<string | null>(
    null,
  )
  const [chart, setChart] = useState<ChartConfig | null>(null)
  const [writeResult, setWriteResult] = useState<{
    affected_rows: number | null
    sql: string
  } | null>(null)

  const abortRef = useRef<AbortController | null>(null)
  const traceIdRef = useRef(0)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const [exRes, schRes] = await Promise.all([
          apiFetch('/api/examples'),
          apiFetch('/api/schema'),
        ])
        if (!exRes.ok || !schRes.ok) {
          throw new Error('加载示例或表结构失败')
        }
        const exData = await exRes.json()
        const schData = await schRes.json()
        if (cancelled) return
        setExamples(exData.examples ?? [])
        setTables(schData.tables ?? [])
      } catch (e) {
        if (!cancelled) {
          setSideError(e instanceof Error ? e.message : '侧栏数据加载失败')
        }
      }
    })()
    return () => {
      cancelled = true
      abortRef.current?.abort()
    }
  }, [])

  function handleLogout() {
    abortRef.current?.abort()
    logout()
    navigate('/')
  }

  function resetResult() {
    setAnswer('')
    setSql('')
    setSqlRepaired(false)
    setColumns([])
    setRows([])
    setError(null)
    setTrace([])
    setLatencyMs(null)
    setGuardrailPassed(false)
    setClarificationHint(null)
    setChart(null)
    setWriteResult(null)
    traceIdRef.current = 0
  }

  function pushTrace(event: string, summary: string) {
    traceIdRef.current += 1
    setTrace((prev) => [
      ...prev,
      { id: traceIdRef.current, event, summary },
    ])
  }

  async function handleSubmit(e?: FormEvent) {
    e?.preventDefault()
    const q = question.trim()
    if (!q || streaming) return

    abortRef.current?.abort()
    const ac = new AbortController()
    abortRef.current = ac

    resetResult()
    setStreaming(true)
    setTraceOpen(true)

    try {
      await streamChat({
        question: q,
        sessionId: 'default',
        signal: ac.signal,
        onEvent: (event, data) => {
          switch (event) {
            case 'run_start':
              pushTrace(
                event,
                `request ${String(data.request_id ?? '')}`.trim(),
              )
              break
            case 'node_start':
              pushTrace(event, `开始 ${String(data.node ?? '')}`)
              break
            case 'node_end': {
              const node = String(data.node ?? '')
              const summary = String(data.summary ?? '完成')
              pushTrace(event, `${node}: ${summary}`)
              if (node === 'SQLGuardrail' && summary !== 'rejected') {
                setGuardrailPassed(true)
              }
              break
            }
            case 'sql':
              setSql(String(data.sql ?? ''))
              setSqlRepaired(Boolean(data.repaired))
              pushTrace(event, '收到 SQL')
              break
            case 'rows': {
              const cols = Array.isArray(data.columns)
                ? (data.columns as string[])
                : []
              const rowList = Array.isArray(data.rows)
                ? (data.rows as Record<string, unknown>[])
                : []
              setColumns(cols)
              setRows(rowList)
              pushTrace(event, `${rowList.length} 行`)
              break
            }
            case 'answer':
              setAnswer(String(data.text ?? ''))
              pushTrace(event, '结论已生成')
              break
            case 'tool_start':
              pushTrace(
                event,
                `调用 ${String(data.tool ?? '')}`.trim(),
              )
              break
            case 'tool_end': {
              const riskPrefix =
                data.risk_level === 'high' ? '⚠ high · ' : ''
              pushTrace(
                event,
                `${riskPrefix}${String(data.tool ?? '')}: ${String(data.status ?? 'done')}`,
              )
              break
            }
            case 'chart':
              setChart({
                type: String(data.type ?? 'table'),
                x: String(data.x ?? ''),
                y: String(data.y ?? ''),
                title: data.title ? String(data.title) : undefined,
              })
              pushTrace(event, String(data.type ?? 'chart'))
              break
            case 'write_result':
              setWriteResult({
                affected_rows:
                  typeof data.affected_rows === 'number'
                    ? data.affected_rows
                    : null,
                sql: String(data.sql ?? ''),
              })
              pushTrace(
                event,
                `写操作 · ${
                  typeof data.affected_rows === 'number'
                    ? data.affected_rows
                    : '?'
                } 行`,
              )
              break
            case 'route_decision':
              pushTrace(
                event,
                `${String(data.route_mode ?? '')} · ${String(data.route_source ?? '')}`,
              )
              break
            case 'error':
              setError(String(data.message ?? '分析失败'))
              pushTrace(event, String(data.message ?? 'error'))
              break
            case 'done':
              if (typeof data.latency_ms === 'number') {
                setLatencyMs(data.latency_ms)
              }
              if (data.need_clarification === true) {
                setError(null)
                setClarificationHint('需要补充信息后继续')
              }
              pushTrace(event, `完成 ${data.latency_ms ?? ''}ms`)
              break
            default:
              pushTrace(event, JSON.stringify(data).slice(0, 80))
          }
        },
      })
    } catch (err) {
      if ((err as Error).name === 'AbortError') return
      setError(err instanceof Error ? err.message : '请求失败')
    } finally {
      setStreaming(false)
    }
  }

  return (
    <div className="flex min-h-screen bg-bg text-ink">
      {/* Left sidebar */}
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
            <h2 className="text-[11px] font-medium uppercase tracking-wider text-muted">
              示例问题
            </h2>
            <ul className="mt-2 space-y-1">
              {examples.map((ex) => (
                <li key={ex.id}>
                  <button
                    type="button"
                    onClick={() => setQuestion(ex.question)}
                    className="w-full rounded-md px-2 py-1.5 text-left text-xs leading-snug text-ink transition-colors hover:bg-accent-soft hover:text-accent"
                  >
                    {ex.question}
                  </button>
                </li>
              ))}
            </ul>
          </section>

          <section className="mt-6">
            <h2 className="text-[11px] font-medium uppercase tracking-wider text-muted">
              数据表
            </h2>
            <ul className="mt-2 space-y-1">
              {tables.map((t) => (
                <li
                  key={t.name}
                  className="rounded-md px-2 py-1 font-mono text-xs text-muted"
                  title={t.columns.map((c) => c.name).join(', ')}
                >
                  {t.name}
                  <span className="ml-1 text-[10px] opacity-70">
                    ({t.columns.length})
                  </span>
                </li>
              ))}
            </ul>
          </section>
        </div>

        <div className="border-t border-line p-4">
          <button
            type="button"
            onClick={handleLogout}
            className="w-full rounded-lg border border-line px-3 py-2 text-sm text-muted transition-colors hover:border-accent hover:text-accent"
          >
            退出登录
          </button>
        </div>
      </aside>

      {/* Main panel */}
      <main className="flex min-w-0 flex-1 flex-col">
        <header className="border-b border-line bg-surface px-6 py-4">
          <h1 className="font-display text-lg">分析工作台</h1>
          <p className="text-xs text-muted">
            自然语言提问 · SQL 经 Guardrail 后执行
          </p>
        </header>

        <div className="flex-1 space-y-4 overflow-y-auto px-6 py-5">
          <form onSubmit={handleSubmit} className="flex gap-3">
            <textarea
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              rows={3}
              placeholder="输入分析问题，或从左侧选择示例…"
              className="min-h-[5rem] flex-1 resize-y rounded-xl border border-line bg-surface px-4 py-3 text-sm outline-none ring-accent/30 focus:ring-2"
              disabled={streaming}
            />
            <button
              type="submit"
              disabled={streaming || !question.trim()}
              className="self-end rounded-xl bg-accent px-5 py-3 text-sm font-medium text-white transition-opacity disabled:opacity-40"
            >
              {streaming ? '分析中…' : '发送'}
            </button>
          </form>

          {error && (
            <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
              {error}
            </div>
          )}

          {answer && (
            <section className="rounded-xl border border-line bg-surface p-4">
              <h2 className="text-xs font-medium uppercase tracking-wider text-muted">
                回答
              </h2>
              {clarificationHint && (
                <p className="mt-2 text-xs text-muted">{clarificationHint}</p>
              )}
              <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed">
                {answer}
              </p>
            </section>
          )}

          {sql && (
            <section className="rounded-xl border border-line bg-surface p-4">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-xs font-medium uppercase tracking-wider text-muted">
                  SQL
                </h2>
                {guardrailPassed && (
                  <span className="rounded-full bg-accent-soft px-2 py-0.5 text-[11px] text-accent">
                    已通过安全校验
                  </span>
                )}
                {sqlRepaired && (
                  <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[11px] text-amber-800">
                    已根据执行错误自动修复
                  </span>
                )}
              </div>
              <pre className="mt-2 overflow-x-auto rounded-lg bg-bg p-3 font-mono text-xs leading-relaxed text-ink">
                {sql}
              </pre>
            </section>
          )}

          {writeResult && (
            <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
              写操作已成功执行
              {writeResult.affected_rows != null
                ? ` · 影响 ${writeResult.affected_rows} 行`
                : ''}
            </div>
          )}

          {columns.length > 0 && (
            <section className="rounded-xl border border-line bg-surface p-4">
              <h2 className="text-xs font-medium uppercase tracking-wider text-muted">
                查询结果 · {rows.length} 行
              </h2>
              <div className="mt-3 max-h-80 overflow-auto">
                <table className="min-w-full border-collapse text-left text-xs">
                  <thead>
                    <tr className="border-b border-line text-muted">
                      {columns.map((c) => (
                        <th key={c} className="sticky top-0 bg-surface px-3 py-2 font-medium">
                          {c}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row, i) => (
                      <tr key={i} className="border-b border-line/60">
                        {columns.map((c) => (
                          <td key={c} className="px-3 py-2 font-mono whitespace-nowrap">
                            {formatCell(row[c])}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          <ResultChart chart={chart} rows={rows} />

          <section className="rounded-xl border border-line bg-surface">
            <button
              type="button"
              onClick={() => setTraceOpen((v) => !v)}
              className="flex w-full items-center justify-between px-4 py-3 text-left text-sm"
            >
              <span className="text-xs font-medium uppercase tracking-wider text-muted">
                Agent Trace
                {latencyMs != null ? ` · ${latencyMs}ms` : ''}
                {trace.length ? ` · ${trace.length}` : ''}
              </span>
              <span className="text-muted">{traceOpen ? '收起' : '展开'}</span>
            </button>
            {traceOpen && (
              <ol className="space-y-1 border-t border-line px-4 py-3 font-mono text-[11px] text-muted">
                {trace.length === 0 && (
                  <li className="text-muted">提交问题后显示节点事件</li>
                )}
                {trace.map((t) => (
                  <li key={t.id} className="flex gap-2">
                    <span className="shrink-0 text-accent">{t.event}</span>
                    <span>{t.summary}</span>
                  </li>
                ))}
              </ol>
            )}
          </section>
        </div>
      </main>
    </div>
  )
}

function formatCell(value: unknown): string {
  if (value == null) return ''
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}
