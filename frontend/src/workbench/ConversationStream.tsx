import type {ChatMessage} from '../types'
import {InterruptPanel} from './InterruptPanel'
import {ResultTable} from './ResultTable'
import {ChartRenderer} from './ChartRenderer'
import {ThinkingTrace} from './ThinkingTrace'

export function ConversationStream({
  messages,
  role,
  liveId,
  onResume,
  onPick,
  onToggleThink,
  suggestions = [],
  disabled = false,
}: {
  messages: ChatMessage[]
  role: 'analyst' | 'operator'
  liveId?: string
  onResume: (payload: Record<string, unknown>) => void
  onPick?: (text: string) => void
  onToggleThink: (id: string) => void
  suggestions?: string[]
  disabled?: boolean
}) {
  const lastUser = [...messages].reverse().find(item => item.role === 'user')?.text
  return (
    <div className="stream">
      {messages.map(message => {
        const live = message.id === liveId
        const open = message.thinkingOpen ?? live
        return (
          <article key={message.id} className={`bubble ${message.role}`}>
            {message.role === 'assistant' ? (
              <ThinkingTrace
                steps={message.thinking ?? []}
                open={open}
                live={live}
                onToggle={() => onToggleThink(message.id)}
              />
            ) : null}
            {message.text ? <p className="answer">{message.text}</p> : null}
            {message.result ? (
              <>
                <ResultTable result={message.result} />
                <ChartRenderer result={message.result} />
              </>
            ) : null}
            {message.interrupt ? (
              <InterruptPanel
                interrupt={message.interrupt}
                role={role}
                onResume={onResume}
                onPick={onPick}
                suggestions={suggestions}
                exclude={lastUser}
                disabled={disabled}
              />
            ) : null}
          </article>
        )
      })}
    </div>
  )
}
