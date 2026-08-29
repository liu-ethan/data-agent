import type {HitlChange, HitlOption, Role, WritePreview} from '../types'

const DEFAULT_HINTS = ['本月 GMV 是多少？', '各品类 GMV', '本月退款率如何？']

function optionsOf(interrupt: WritePreview): HitlOption[] {
  return interrupt.options ?? interrupt.candidates ?? []
}

function looksTechnical(text?: string): boolean {
  if (!text) return false
  const trimmed = text.trim()
  if (/^[A-Z][A-Z0-9_]+$/.test(trimmed)) return true
  if (/^[A-Za-z_]\w*\.[A-Za-z_]\w*$/.test(trimmed)) return true
  if (/\brequired\b/i.test(trimmed)) return true
  if (/allowlist/i.test(trimmed)) return true
  return false
}

function kicker(interrupt: WritePreview, isWrite: boolean, hasChoices: boolean): string {
  if (isWrite) return '写入预览'
  if (hasChoices) return '请选择'
  if (interrupt.kind === 'query_error') return '查询需要补充'
  if (interrupt.kind === 'clarify') return '请选择'
  return '需要确认'
}

function questionOf(interrupt: WritePreview, hasChoices: boolean): string | undefined {
  const preferred = [
    interrupt.message,
    interrupt.error_message,
    interrupt.schema_gap?.missing_concept,
    interrupt.ambiguous?.reason,
  ]
  const found = preferred.find(item => item && !looksTechnical(item))
  if (found) return found
  if (hasChoices && interrupt.kind !== 'write_preview') return '这个问法还缺条件，选一个继续：'
  return undefined
}

function fallbackChoices(
  interrupt: WritePreview,
  suggestions: string[],
  exclude: string | undefined,
): HitlOption[] {
  const seen = new Set<string>()
  const out: HitlOption[] = []
  for (const raw of suggestions) {
    const label = raw.trim()
    if (!label || label === exclude || seen.has(label)) continue
    seen.add(label)
    out.push({id: label, label})
  }
  if (out.length) return out
  const failed = interrupt.query || exclude
  return DEFAULT_HINTS.filter(item => item !== failed).map(label => ({id: label, label}))
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
  onPick,
  suggestions = [],
  exclude,
  disabled = false,
}: {
  interrupt: WritePreview
  role: Role
  onResume: (payload: Record<string, unknown>) => void
  onPick?: (text: string) => void
  suggestions?: string[]
  exclude?: string
  disabled?: boolean
}) {
  const isWrite = interrupt.kind === 'write_preview' || Boolean(interrupt.operation_id)
  if (interrupt.resolved && !isWrite) return null
  const canApprove = role === 'operator' && isWrite
  const given = optionsOf(interrupt)
  const choices =
    interrupt.resolved || given.length || isWrite ? given : fallbackChoices(interrupt, suggestions, exclude)
  const detail = questionOf(interrupt, choices.length > 0)
  const notFound = interrupt.status === 'not_found' && !choices.length && !detail
  const locked = disabled || Boolean(interrupt.resolved)

  function pick(option: HitlOption) {
    if (locked) return
    const label = option.label || option.id
    if (!isWrite && onPick) {
      onPick(label)
      return
    }
    onResume({selected_id: option.id})
  }

  return (
    <div className="interrupt">
      <p className="interrupt-kicker">{kicker(interrupt, isWrite, choices.length > 0)}</p>
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
      {choices.length && !interrupt.resolved ? (
        <div className="interrupt-choices">
          {choices.map(option => (
            <button key={option.id} type="button" disabled={locked} onClick={() => pick(option)}>
              {option.label || option.id}
            </button>
          ))}
        </div>
      ) : null}
      {isWrite && !interrupt.resolved ? (
        <div className="interrupt-actions">
          {canApprove ? (
            <button type="button" className="confirm" disabled={locked} onClick={() => onResume({approved: true})}>
              确认
            </button>
          ) : (
            <p className="interrupt-note">分析师不能确认写入，请管理员在同一账号下批准。</p>
          )}
          <button type="button" disabled={locked} onClick={() => onResume({approved: false})}>
            取消
          </button>
        </div>
      ) : null}
    </div>
  )
}
