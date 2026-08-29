import type {HitlChange, HitlOption, Role, WritePreview} from '../types'

function optionsOf(interrupt: WritePreview): HitlOption[] {
  return interrupt.options ?? interrupt.candidates ?? []
}

function kicker(interrupt: WritePreview, isWrite: boolean): string {
  if (isWrite) return '写入预览'
  if (interrupt.kind === 'query_error') return '查询需要补充'
  if (interrupt.kind === 'clarify') return '请选择'
  return '需要确认'
}

function changeText(item: HitlChange | string): string {
  if (typeof item === 'string') return item
  const from = item.from == null ? '' : String(item.from)
  const to = item.to == null ? '' : String(item.to)
  const field = item.field ? `${item.field} ` : ''
  return `${item.id}：${field}${from} → ${to}`.trim()
}

export function InterruptPanel({
  interrupt,
  role,
  onResume,
}: {
  interrupt: WritePreview
  role: Role
  onResume: (payload: Record<string, unknown>) => void
}) {
  const isWrite = interrupt.kind === 'write_preview' || Boolean(interrupt.operation_id)
  const canApprove = role === 'operator' && isWrite
  const choices = optionsOf(interrupt)
  const detail =
    interrupt.message ||
    interrupt.error_message ||
    interrupt.schema_gap?.missing_concept ||
    interrupt.ambiguous?.reason
  const notFound = interrupt.status === 'not_found' && !choices.length && !detail

  return (
    <div className="interrupt">
      <p className="interrupt-kicker">{kicker(interrupt, isWrite)}</p>
      {interrupt.error_code ? <p className="interrupt-code">{interrupt.error_code}</p> : null}
      {detail ? <p>{detail}</p> : null}
      {notFound ? <p>未查到可选项，请换一种问法。</p> : null}
      {interrupt.operation_type ? <p>操作：{interrupt.operation_type}</p> : null}
      {interrupt.affected_rows != null ? <p>影响行数：{interrupt.affected_rows}</p> : null}
      {interrupt.changes?.length ? (
        <ul>
          {interrupt.changes.map((item, index) => (
            <li key={typeof item === 'string' ? item : `${item.id}-${index}`}>{changeText(item)}</li>
          ))}
        </ul>
      ) : null}
      {interrupt.rows?.length ? (
        <pre className="interrupt-rows">{JSON.stringify(interrupt.rows.slice(0, 8), null, 2)}</pre>
      ) : null}
      {choices.length ? (
        <div className="interrupt-choices">
          {choices.map(option => (
            <button key={option.id} type="button" onClick={() => onResume({selected_id: option.id})}>
              {option.label || option.id}
            </button>
          ))}
        </div>
      ) : null}
      {isWrite ? (
        <div className="interrupt-actions">
          {canApprove ? (
            <button type="button" className="confirm" onClick={() => onResume({approved: true})}>
              确认
            </button>
          ) : (
            <p className="interrupt-note">分析师不能确认写入，请管理员在同一账号下批准。</p>
          )}
          <button type="button" onClick={() => onResume({approved: false})}>
            取消
          </button>
        </div>
      ) : null}
    </div>
  )
}
