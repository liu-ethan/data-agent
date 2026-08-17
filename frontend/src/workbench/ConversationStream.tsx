import type {ChartDsl, Interrupt, Message, ResultPage, StreamEvent} from '../types'
import {ChartRenderer} from './ChartRenderer'
import {InterruptPanel} from './InterruptPanel'
import {ResultTable} from './ResultTable'
import {TraceDrawer} from './TraceDrawer'

export function ConversationStream({
  messages,
  interrupt,
  chartDsl,
  result,
  busy,
  connection,
  error,
  recommended,
  onPick,
  onResume,
  onDownload,
  onPage,
  onReconnectThread,
  traceId,
  requestId,
  errorCode,
}: {
  messages: Message[]
  interrupt?: Interrupt
  chartDsl?: ChartDsl
  result?: ResultPage
  busy: boolean
  connection: 'ready' | 'running' | 'reconnecting' | 'offline'
  error?: string
  recommended: string[]
  onPick: (question: string) => void
  onResume: (answer: string) => void
  onDownload: () => void
  onPage?: (offset: number) => void
  onReconnectThread?: () => void
  traceId?: string
  requestId?: string
  errorCode?: string
}) {
  return (
    <section className="conversation" aria-label="对话与结果流">
      <div className="conversation-scroll">
        {messages.length === 0
          ? <EmptyState recommended={recommended} onPick={onPick} />
          : messages.map((message, index) => (
              <article
                key={`${message.role}-${index}-${message.content.slice(0, 24)}`}
                className={`bubble ${message.role === 'user' ? 'bubble-user' : 'bubble-assistant'}`}
              >
                <header className="bubble-head">
                  <span className="bubble-label">{message.role === 'user' ? '你' : 'Runtime'}</span>
                </header>
                <div className="bubble-body">{message.content || '运行结束'}</div>
              </article>
            ))}
        {busy && (
          <div className="bubble bubble-assistant bubble-running" aria-live="polite">
            <header className="bubble-head"><span className="bubble-label">Runtime</span></header>
            <div className="bubble-body">
              {connection === 'reconnecting' ? '正在恢复实时连接…' : '正在理解问题'}
            </div>
          </div>
        )}
        {result && <ResultTable result={result} onDownload={onDownload} onPage={onPage} />}
        {chartDsl && result && <ChartRenderer dsl={chartDsl} result={result} />}
        {error && (
          <div className="error-banner" role="alert">
            <p>{error}</p>
            {connection === 'offline' && onReconnectThread && (
              <button type="button" onClick={onReconnectThread}>继续查看线程</button>
            )}
          </div>
        )}
        <TraceDrawer traceId={traceId} requestId={requestId} errorCode={errorCode} />
      </div>
      {interrupt && <InterruptPanel interrupt={interrupt} busy={busy} onResume={onResume} />}
    </section>
  )
}

function EmptyState({
  recommended,
  onPick,
}: {
  recommended: string[]
  onPick: (question: string) => void
}) {
  return (
    <section className="empty-state" aria-label="新会话">
      <p className="empty-eyebrow">新问题</p>
      <h1 className="empty-headline">提出一个可验证的数据问题</h1>
      <p className="empty-sub">回答会附带运行节点、权限和结果引用，而不是聊天记录。</p>
      <div className="recommended" aria-label="推荐问题">
        {recommended.slice(0, 10).map(question => (
          <button type="button" key={question} className="recommended-card" onClick={() => onPick(question)}>
            {question}
          </button>
        ))}
      </div>
    </section>
  )
}
