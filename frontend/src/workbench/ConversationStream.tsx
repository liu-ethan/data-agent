import type {ChatMessage} from '../types'
import {InterruptPanel} from './InterruptPanel'
import {ResultTable} from './ResultTable'
import {ChartRenderer} from './ChartRenderer'

export function ConversationStream({
  messages,
  role,
  onResume,
}: {
  messages: ChatMessage[]
  role: 'analyst' | 'operator'
  onResume: (payload: Record<string, unknown>) => void
}) {
  return (
    <div className="stream">
      {messages.map(message => (
        <article key={message.id} className={`bubble ${message.role}`}>
          {message.text ? <p>{message.text}</p> : null}
          {message.result ? (
            <>
              <ResultTable result={message.result} />
              <ChartRenderer result={message.result} />
            </>
          ) : null}
          {message.interrupt ? (
            <InterruptPanel interrupt={message.interrupt} role={role} onResume={onResume} />
          ) : null}
        </article>
      ))}
    </div>
  )
}
