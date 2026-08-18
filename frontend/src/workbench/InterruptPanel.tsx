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
  const preview = interrupt.preview
  const isWriteApproval = interrupt.reason === 'WRITE_APPROVAL' && preview != null

  function submit(event: FormEvent) {
    event.preventDefault()
    const answer = clarification.trim()
    if (!answer || busy) return
    onResume(answer)
    setClarification('')
  }

  return (
    <form className="interrupt-panel" onSubmit={submit} aria-live="polite">
      <label htmlFor={isWriteApproval ? undefined : 'clarify-input'}>
        {isWriteApproval ? '确认写入变更：' : '补充信息以继续：'}
        <b>{interrupt.question}</b>
      </label>
      {isWriteApproval && preview && <MutationPreviewTable preview={preview} />}
      {!isWriteApproval && (
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
      )}
      {(interrupt.candidates ?? []).length > 0 && (
        <div className="clarify-candidates">
          {(interrupt.candidates ?? []).map(candidate => (
            <button
              key={candidate}
              type="button"
              className={candidate === '确认执行' ? 'clarify-send' : 'clarify-chip'}
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

function MutationPreviewTable({
  preview,
}: {
  preview: NonNullable<Interrupt['preview']>
}) {
  const entries = Object.entries(preview.diff ?? {})
  return (
    <div className="mutation-preview" aria-label="写入预览">
      <p className="mutation-preview-meta">
        <span>{preview.target}</span>
        <span>预计影响 {preview.estimated_affected_rows} 行</span>
        <span>风险 {preview.risk_level}</span>
      </p>
      <table>
        <thead>
          <tr>
            <th>字段</th>
            <th>修改前</th>
            <th>修改后</th>
          </tr>
        </thead>
        <tbody>
          {entries.map(([field, change]) => {
            const diff = asDiff(change)
            return (
              <tr key={field}>
                <td>{field}</td>
                <td>{stringifyDiff(diff.before)}</td>
                <td>{stringifyDiff(diff.after)}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function asDiff(value: unknown): {before?: unknown; after?: unknown} {
  if (value && typeof value === 'object' && 'before' in value && 'after' in value) {
    return value as {before?: unknown; after?: unknown}
  }
  return {after: value}
}

function stringifyDiff(value: unknown): string {
  if (value == null) return '—'
  return String(value)
}
