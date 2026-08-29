import type {ThinkStep} from '../types'

export function ThinkingTrace({
  steps,
  open,
  live,
  onToggle,
}: {
  steps: ThinkStep[]
  open: boolean
  live: boolean
  onToggle: () => void
}) {
  if (!steps.length && !live) return null
  const current = steps[steps.length - 1]
  const title = live
    ? `思考中 · ${current?.label ?? '准备'}`
    : `已思考${steps.length ? ` ${steps.length} 步` : ''}`
  return (
    <div className={`think ${open ? 'open' : 'collapsed'} ${live ? 'live' : ''}`}>
      <button
        type="button"
        className="think-toggle"
        aria-expanded={open}
        onClick={onToggle}
      >
        <span className={`think-chevron ${open ? 'down' : ''}`} aria-hidden="true" />
        {title}
      </button>
      {open ? (
        <div className="think-body">
          {steps.map((step, index) => (
            <p key={`${step.node}-${index}`}>
              <span className="think-label">{step.label}</span>
              {step.text}
            </p>
          ))}
          {live && !steps.length ? <p>正在准备这一轮。</p> : null}
        </div>
      ) : null}
    </div>
  )
}
