import {useState, type FormEvent, type KeyboardEvent} from 'react'

export function ChatComposer({
  disabled,
  onSend,
}: {
  disabled: boolean
  onSend: (text: string) => void
}) {
  const [value, setValue] = useState('')

  function submit(event?: FormEvent) {
    event?.preventDefault()
    const text = value.trim()
    if (!text || disabled) return
    onSend(text)
    setValue('')
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      submit()
    }
  }

  return (
    <form className="composer" onSubmit={submit}>
      <textarea
        name="message"
        placeholder="问一句经营数据，或从上方推荐问开始"
        value={value}
        disabled={disabled}
        onChange={event => setValue(event.target.value)}
        onKeyDown={onKeyDown}
        rows={3}
      />
      <div className="composer-bar">
        <span className="composer-hint">Enter 发送 · Shift+Enter 换行</span>
        <button type="submit" disabled={disabled || !value.trim()}>
          发送
        </button>
      </div>
    </form>
  )
}
