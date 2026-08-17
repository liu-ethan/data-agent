import {useState, type KeyboardEvent} from 'react'

export function ChatComposer({
  busy,
  timezone,
  empty,
  onSend,
  onStop,
}: {
  busy: boolean
  timezone: string
  empty: boolean
  onSend: (text: string) => void
  onStop: () => void
}) {
  const [question, setQuestion] = useState('')
  const placeholder = empty ? '例如：昨天各品类的 GMV 是多少？' : '继续提问，例如：拆到一级品类'

  function submit() {
    const text = question.trim()
    if (!text || busy) return
    onSend(text)
    setQuestion('')
  }

  function handleKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      submit()
    }
  }

  return (
    <div className="composer">
      <textarea
        aria-label="问题"
        value={question}
        onChange={event => setQuestion(event.target.value)}
        onKeyDown={handleKey}
        placeholder={placeholder}
      />
      {busy
        ? <button type="button" className="composer-stop" aria-label="停止" onClick={onStop}>停止</button>
        : (
          <button
            type="button"
            className="composer-send"
            aria-label="发送"
            onClick={submit}
            disabled={!question.trim()}
          >
            发送
          </button>
        )}
      <span className="composer-hint">Enter 发送 · Shift + Enter 换行 · 默认时区 {timezone}</span>
    </div>
  )
}
