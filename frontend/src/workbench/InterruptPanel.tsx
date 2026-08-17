import {useState, type FormEvent} from 'react'
import type {Interrupt} from '../types'

export function InterruptPanel({
  interrupt,
  busy,
  onResume,
}: {
  interrupt: Interrupt
  busy: boolean
  onResume: (answer: string) => void
}) {
  const [clarification, setClarification] = useState('')

  function submit(event: FormEvent) {
    event.preventDefault()
    const answer = clarification.trim()
    if (!answer || busy) return
    onResume(answer)
    setClarification('')
  }

  return (
    <form className="interrupt-panel" onSubmit={submit} aria-live="polite">
      <label htmlFor="clarify-input">
        补充信息以继续：
        <b>{interrupt.question}</b>
      </label>
      <div className="clarify-input-row">
        <input
          id="clarify-input"
          name="clarification"
          value={clarification}
          onChange={event => setClarification(event.target.value)}
          placeholder="用一句话回答"
        />
        <button type="submit" className="clarify-send" disabled={busy || !clarification.trim()}>
          继续
        </button>
      </div>
      {(interrupt.candidates ?? []).length > 0 && (
        <div className="clarify-candidates">
          {(interrupt.candidates ?? []).map(candidate => (
            <button
              key={candidate}
              type="button"
              className="clarify-chip"
              onClick={() => onResume(candidate)}
              disabled={busy}
            >
              {candidate}
            </button>
          ))}
        </div>
      )}
    </form>
  )
}
