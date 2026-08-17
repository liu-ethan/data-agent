import type {Action, StreamEvent} from '../types'

const STAGES: Action[] = ['RETRIEVE', 'GENERATE', 'EXECUTE', 'RESPOND']

export function RunEvidenceRail({
  events,
  liveMessage,
}: {
  events: StreamEvent[]
  liveMessage?: string
}) {
  const rows = STAGES.map((stage, index) => {
    const completed = [...events].reverse().find(event => event.action === stage && event.event === 'node.completed')
    const started = [...events].reverse().find(event => event.action === stage && event.event === 'node.started')
    const failed = completed?.error_code || (started?.error_code && !completed)
    const status = completed ? 'done' : started ? 'running' : 'idle'
    return {
      stage,
      index,
      status,
      duration: completed?.duration_ms ?? null,
      error: completed?.error_code ?? started?.error_code ?? null,
      failed: Boolean(failed),
    }
  })
  const current = rows.find(row => row.status === 'running') ?? rows.filter(row => row.status === 'done').at(-1)

  return (
    <aside className="evidence-rail" aria-label="证据栏">
      <header className="evidence-head">
        <p className="evidence-kicker">运行证据</p>
        <h2>公开状态脊柱</h2>
      </header>
      <p className="evidence-live" aria-live="polite">
        {liveMessage ?? (current
          ? `${current.stage} ${current.status === 'running' ? '进行中' : '已完成'}`
          : '等待开始')}
      </p>
      <ol className="evidence-spine">
        {rows.map(row => (
          <li key={row.stage} className={`evidence-step ${row.status}${row.failed ? ' failed' : ''}`}>
            <span className="evidence-node" aria-hidden>
              {row.status === 'done' ? '✓' : row.index + 1}
            </span>
            <div>
              <p className="evidence-action">{row.stage}</p>
              <p className="evidence-meta">
                {row.status === 'done' ? '完成' : row.status === 'running' ? '运行中' : '等待中'}
                {row.duration !== null ? ` · ${Math.round(row.duration)} ms` : ''}
                {row.error ? ` · ${row.error}` : ''}
              </p>
            </div>
          </li>
        ))}
      </ol>
    </aside>
  )
}
