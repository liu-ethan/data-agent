import ResultChart, { type ChartConfig } from './ResultChart'

export type TraceEntry = {
  id: number
  event: string
  summary: string
}

export type TurnView = {
  id: string
  question: string
  answer: string
  sql: string
  sqlRepaired: boolean
  guardrailPassed: boolean
  columns: string[]
  rows: Record<string, unknown>[]
  chart: ChartConfig | null
  writeResult: { affected_rows: number | null; sql: string } | null
  trace: TraceEntry[]
  error: string | null
  errorTraceId: string | null
  clarificationHint: string | null
  latencyMs: number | null
  streaming: boolean
  fromHistory: boolean
  open: { sql: boolean; rows: boolean; chart: boolean; trace: boolean }
}

type TurnSection = keyof TurnView['open']

type Props = {
  turn: TurnView
  onToggle: (section: TurnSection) => void
}

export default function TurnCard({ turn, onToggle }: Props) {
  const hasRows = turn.columns.length > 0 && turn.rows.length > 0
  const hasChart =
    turn.chart != null &&
    turn.chart.type !== 'table' &&
    Boolean(turn.chart.x) &&
    Boolean(turn.chart.y) &&
    turn.rows.length > 0
  const hasTrace = turn.trace.length > 0

  return (
    <article className="space-y-3">
      <div className="flex justify-end">
        <div className="max-w-[82%] rounded-2xl rounded-br-md bg-accent px-4 py-3 text-sm leading-relaxed text-white shadow-sm">
          {turn.question}
        </div>
      </div>

      <div className="mr-auto max-w-4xl rounded-2xl rounded-tl-md border border-line bg-surface shadow-sm">
        <div className="px-5 py-4">
          <div className="flex items-center justify-between gap-3">
            <h2 className="text-[11px] font-medium uppercase tracking-wider text-muted">
              分析结论
            </h2>
            <div className="flex items-center gap-2">
              {turn.fromHistory && (
                <span className="text-[10px] text-muted">历史恢复</span>
              )}
              {turn.streaming && (
                <span className="inline-flex items-center gap-1.5 text-[11px] text-accent">
                  <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent" />
                  分析中
                </span>
              )}
            </div>
          </div>

          {turn.clarificationHint && (
            <p className="mt-2 text-xs text-muted">{turn.clarificationHint}</p>
          )}
          {turn.answer ? (
            <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed">
              {turn.answer}
            </p>
          ) : turn.streaming ? (
            <p className="mt-2 text-sm text-muted">正在理解问题并生成分析…</p>
          ) : null}

          {turn.error && (
            <div className="mt-3 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
              <p>{turn.error}</p>
              {turn.errorTraceId && (
                <p className="mt-1 font-mono text-xs text-red-700/80">
                  trace_id: {turn.errorTraceId}
                </p>
              )}
            </div>
          )}

          {turn.writeResult && (
            <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
              写操作已成功执行
              {turn.writeResult.affected_rows != null
                ? ` · 影响 ${turn.writeResult.affected_rows} 行`
                : ''}
            </div>
          )}

          <div className="mt-4 flex flex-wrap gap-2">
            {turn.sql && (
              <SectionChip
                label="SQL"
                open={turn.open.sql}
                onClick={() => onToggle('sql')}
              />
            )}
            {hasRows && (
              <SectionChip
                label={`查询结果 · ${turn.rows.length} 行`}
                open={turn.open.rows}
                onClick={() => onToggle('rows')}
              />
            )}
            {hasChart && (
              <SectionChip
                label="图表"
                open={turn.open.chart}
                onClick={() => onToggle('chart')}
              />
            )}
            {hasTrace && (
              <SectionChip
                label={`AGENT TRACE · ${turn.trace.length}`}
                open={turn.open.trace}
                onClick={() => onToggle('trace')}
              />
            )}
            {turn.latencyMs != null && (
              <span className="self-center text-[11px] text-muted">
                {turn.latencyMs}ms
              </span>
            )}
          </div>
        </div>

        {turn.sql && turn.open.sql && (
          <section className="border-t border-line px-5 py-4">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-xs font-medium uppercase tracking-wider text-muted">
                SQL
              </h3>
              {turn.guardrailPassed && (
                <span className="rounded-full bg-accent-soft px-2.5 py-0.5 text-[11px] leading-5 text-accent">
                  已通过权限校验 / 沙箱检查 / 安全审核
                </span>
              )}
              {turn.sqlRepaired && (
                <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[11px] text-amber-800">
                  已根据执行错误自动修复
                </span>
              )}
            </div>
            <pre className="mt-2 overflow-x-auto rounded-lg bg-bg p-3 font-mono text-xs leading-relaxed text-ink">
              {turn.sql}
            </pre>
          </section>
        )}

        {hasRows && turn.open.rows && (
          <section className="border-t border-line px-5 py-4">
            <h3 className="mb-2 text-xs font-medium uppercase tracking-wider text-muted">
              查询结果
            </h3>
            <div className="max-h-96 overflow-auto">
              <table className="min-w-full border-collapse text-left text-xs">
                <thead>
                  <tr className="border-b border-line text-muted">
                    {turn.columns.map((column) => (
                      <th
                        key={column}
                        className="sticky top-0 bg-surface px-3 py-2 font-medium"
                      >
                        {column}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {turn.rows.map((row, index) => (
                    <tr key={index} className="border-b border-line/60">
                      {turn.columns.map((column) => (
                        <td
                          key={column}
                          className="whitespace-nowrap px-3 py-2 font-mono"
                        >
                          {formatCell(row[column])}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {hasChart && turn.open.chart && (
          <div className="border-t border-line [&>section]:border-0">
            <ResultChart chart={turn.chart} rows={turn.rows} />
          </div>
        )}

        {hasTrace && turn.open.trace && (
          <section className="border-t border-line px-5 py-4">
            <h3 className="mb-2 text-xs font-medium uppercase tracking-wider text-muted">
              AGENT TRACE
            </h3>
            <ol className="max-h-80 space-y-1 overflow-auto font-mono text-[11px] text-muted">
              {turn.trace.map((entry) => (
                <li key={entry.id} className="flex gap-2">
                  <span className="shrink-0 text-accent">{entry.event}</span>
                  <span>{entry.summary}</span>
                </li>
              ))}
            </ol>
          </section>
        )}
      </div>
    </article>
  )
}

function SectionChip({
  label,
  open,
  onClick,
}: {
  label: string
  open: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      aria-expanded={open}
      onClick={onClick}
      className={`rounded-full border px-3 py-1 text-[11px] font-medium transition-colors ${
        open
          ? 'border-accent bg-accent-soft text-accent'
          : 'border-line text-muted hover:border-accent hover:text-accent'
      }`}
    >
      {label}
      <span className="ml-1">{open ? '−' : '+'}</span>
    </button>
  )
}

function formatCell(value: unknown): string {
  if (value == null) return ''
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}
