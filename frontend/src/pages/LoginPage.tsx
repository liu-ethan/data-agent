import { useEffect, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth, type UserRole } from '../auth/AuthContext'

const TICKER_ITEMS = [
  {
    q: '上个月 GMV 最高的 5 个渠道是什么？',
    sql: 'SELECT channel, SUM(gmv) FROM orders ... GROUP BY channel LIMIT 5',
    ms: 812,
  },
  {
    q: '哪些商品品类的退款率最高？',
    sql: 'SELECT category, refund_rate FROM ... ORDER BY refund_rate DESC',
    ms: 764,
  },
  {
    q: '最近 30 天每天的订单量和 GMV 趋势如何？',
    sql: 'SELECT order_date, COUNT(*), SUM(gmv) FROM orders ... GROUP BY order_date',
    ms: 690,
  },
  {
    q: '各城市的新用户注册数排名如何？',
    sql: 'SELECT city, COUNT(*) FROM users ... GROUP BY city ORDER BY 2 DESC',
    ms: 733,
  },
]

const AI_HIGHLIGHTS = [
  { title: 'NL → 安全 SQL', detail: '权限校验 + 沙箱执行' },
  { title: '双路径编排', detail: 'ReAct / Coordinator' },
  { title: 'SSE Trace', detail: '节点过程可观测' },
  { title: '多轮记忆', detail: 'Session 槽位追问' },
]

const CAPABILITY_STORY = [
  {
    step: '01',
    title: '理解',
    summary: '意图 + 槽位 / 澄清',
    detail: '识别经营问题与关键条件；信息不足时先追问，不急于生成 SQL。',
  },
  {
    step: '02',
    title: '执行',
    summary: 'Schema Linking → 权限 → 沙箱 → 可修复',
    detail: '把业务语言连接到数据结构，在权限边界内验证、执行并修复查询。',
  },
  {
    step: '03',
    title: '交付',
    summary: '结论 · 表 · 图 · Trace；多轮追问',
    detail: '同时交付可读结论与可核验过程，让下一轮问题沿着上下文继续。',
  },
]

function QueryTicker() {
  const [index, setIndex] = useState(0)

  useEffect(() => {
    const timer = window.setInterval(() => {
      setIndex((current) => (current + 1) % TICKER_ITEMS.length)
    }, 4200)
    return () => window.clearInterval(timer)
  }, [])

  const item = TICKER_ITEMS[index]

  return (
    <div className="rounded-xl border border-line bg-surface/70 px-5 py-4 font-mono text-[13px] leading-relaxed shadow-sm backdrop-blur-sm">
      <div key={index} className="animate-rise space-y-1.5">
        <p className="text-ink">
          <span className="text-accent">{'>'}</span> {item.q}
          <span className="animate-caret text-accent">▍</span>
        </p>
        <p className="truncate text-muted">{item.sql}</p>
        <p className="text-accent">✓ Guardrail 通过 · 已生成结果与图表 · {item.ms}ms</p>
      </div>
    </div>
  )
}

type Mode = 'login' | 'register'

export default function LoginPage() {
  const navigate = useNavigate()
  const { user, loading, login, register } = useAuth()

  const [mode, setMode] = useState<Mode>('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState<UserRole>('analyst')
  const [inviteCode, setInviteCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    if (!loading && user) navigate('/app', { replace: true })
  }, [loading, user, navigate])

  function switchMode(next: Mode) {
    setMode(next)
    setError(null)
  }

  function fillDemoAccount() {
    setMode('login')
    setUsername('demo_analyst')
    setPassword('demo1234')
    setError(null)
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      if (mode === 'login') {
        await login(username, password)
      } else {
        await register(username, password, role, role === 'admin' ? inviteCode : undefined)
      }
      navigate('/app')
    } catch (err) {
      setError(err instanceof Error ? err.message : '请求失败，请稍后重试')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="relative overflow-x-hidden bg-bg">
      {/* Ambient atmosphere: soft accent glow + coordinate-grid texture, kept quiet */}
      <div
        aria-hidden
        className="pointer-events-none absolute -right-40 -top-40 h-[560px] w-[560px] rounded-full opacity-70 blur-3xl"
        style={{ background: 'radial-gradient(circle, var(--accent-soft), transparent 70%)' }}
      />
      <div
        aria-hidden
        className="bg-grid-dots pointer-events-none absolute inset-x-0 top-0 h-[420px] opacity-40 [mask-image:linear-gradient(to_bottom,black,transparent)]"
      />

      <div className="relative mx-auto flex min-h-screen max-w-6xl flex-col px-6 py-8 lg:px-10">
        <header className="flex items-center gap-2.5">
          <span className="flex h-8 w-8 items-center justify-center rounded-md bg-accent text-[13px] font-semibold text-white">
            da
          </span>
          <span className="font-mono text-[13px] tracking-wide text-muted">经营数据分析 Agent</span>
        </header>

        <main className="flex flex-1 flex-col items-center justify-center gap-14 py-10 lg:flex-row lg:items-center lg:gap-20">
          <section className="max-w-xl">
            <p className="animate-rise text-sm font-medium uppercase tracking-[0.18em] text-muted">
              面向电商团队的经营分析助手
            </p>
            <h1 className="animate-rise mt-4 font-display text-[2.75rem] font-medium leading-[1.08] tracking-tight text-ink sm:text-[3.75rem]">
              data-analysis
              <br />
              <span className="relative inline-block text-accent">
                -agent
                <span className="absolute -bottom-1 left-0 h-[3px] w-full origin-left animate-underline bg-accent/70" />
              </span>
            </h1>
            <p
              className="animate-rise mt-6 text-lg leading-relaxed text-muted"
              style={{ animationDelay: '120ms' }}
            >
              用一句自然语言提问，Agent 生成经过安全校验的 SQL，
              秒级返回结论、明细表与自动图表。
            </p>

            <dl
              className="animate-rise mt-7 grid max-w-xl grid-cols-2 border-y border-line"
              style={{ animationDelay: '180ms' }}
            >
              {AI_HIGHLIGHTS.map((item, index) => (
                <div
                  key={item.title}
                  className={`py-3.5 ${
                    index % 2 === 0 ? 'pr-4' : 'border-l border-line pl-4'
                  } ${index < 2 ? 'border-b border-line' : ''}`}
                >
                  <dt className="text-sm font-semibold text-ink">{item.title}</dt>
                  <dd className="mt-1 font-mono text-[11px] leading-5 text-muted">
                    {item.detail}
                  </dd>
                </div>
              ))}
            </dl>

            <div className="mt-6 max-w-md" style={{ animationDelay: '240ms' }}>
              <QueryTicker />
            </div>
          </section>

          <section className="w-full max-w-md">
            <div className="animate-rise rounded-2xl border border-line bg-surface p-7 shadow-[0_20px_60px_-30px_rgba(18,20,26,0.25)]">
              <div className="relative mb-6 grid grid-cols-2 rounded-lg bg-bg p-1 text-sm font-medium">
                <span
                  aria-hidden
                  className={`absolute inset-y-1 w-[calc(50%-4px)] rounded-md bg-surface shadow-sm transition-transform duration-300 ease-out ${
                    mode === 'register' ? 'translate-x-[calc(100%+8px)]' : 'translate-x-0'
                  }`}
                />
                <button
                  type="button"
                  onClick={() => switchMode('login')}
                  className={`relative z-10 rounded-md py-2 transition-colors ${
                    mode === 'login' ? 'text-ink' : 'text-muted'
                  }`}
                >
                  登录
                </button>
                <button
                  type="button"
                  onClick={() => switchMode('register')}
                  className={`relative z-10 rounded-md py-2 transition-colors ${
                    mode === 'register' ? 'text-ink' : 'text-muted'
                  }`}
                >
                  注册
                </button>
              </div>

              <form onSubmit={handleSubmit} className="space-y-4">
                <div>
                  <label htmlFor="username" className="mb-1.5 block text-sm text-muted">
                    用户名
                  </label>
                  <input
                    id="username"
                    required
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    className="w-full rounded-lg border border-line bg-white px-3.5 py-2.5 text-ink outline-none transition-shadow focus:border-accent focus:ring-2 focus:ring-accent/20"
                    placeholder="alice"
                    autoComplete="username"
                  />
                </div>

                <div>
                  <label htmlFor="password" className="mb-1.5 block text-sm text-muted">
                    密码
                  </label>
                  <input
                    id="password"
                    type="password"
                    required
                    minLength={6}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    className="w-full rounded-lg border border-line bg-white px-3.5 py-2.5 text-ink outline-none transition-shadow focus:border-accent focus:ring-2 focus:ring-accent/20"
                    placeholder="••••••••"
                    autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
                  />
                </div>

                {mode === 'register' && (
                  <div>
                    <label htmlFor="role" className="mb-1.5 block text-sm text-muted">
                      角色
                    </label>
                    <select
                      id="role"
                      value={role}
                      onChange={(e) => setRole(e.target.value as UserRole)}
                      className="w-full rounded-lg border border-line bg-white px-3.5 py-2.5 text-ink outline-none transition-shadow focus:border-accent focus:ring-2 focus:ring-accent/20"
                    >
                      <option value="analyst">分析师（analyst）</option>
                      <option value="admin">管理员（admin）</option>
                    </select>
                  </div>
                )}

                <div
                  className={`grid transition-all duration-300 ease-out ${
                    mode === 'register' && role === 'admin'
                      ? 'grid-rows-[1fr] opacity-100'
                      : 'grid-rows-[0fr] opacity-0'
                  }`}
                >
                  <div className="overflow-hidden">
                    <label htmlFor="inviteCode" className="mb-1.5 block text-sm text-muted">
                      邀请码
                    </label>
                    <input
                      id="inviteCode"
                      value={inviteCode}
                      onChange={(e) => setInviteCode(e.target.value)}
                      required={mode === 'register' && role === 'admin'}
                      className="w-full rounded-lg border border-line bg-white px-3.5 py-2.5 text-ink outline-none transition-shadow focus:border-accent focus:ring-2 focus:ring-accent/20"
                      placeholder="管理员邀请码"
                    />
                  </div>
                </div>

                {error && (
                  <p className="rounded-lg border border-[#f2c9c4] bg-[#fdf1ef] px-3 py-2 text-sm text-[#b3261e]">
                    {error}
                  </p>
                )}

                <button
                  type="submit"
                  disabled={submitting}
                  className="w-full rounded-lg bg-accent py-2.5 font-medium text-white transition-transform duration-150 hover:scale-[1.01] active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {submitting ? '处理中…' : mode === 'login' ? '登录' : '注册并进入'}
                </button>
              </form>

              <button
                type="button"
                onClick={fillDemoAccount}
                className="mt-4 w-full text-center text-sm text-muted transition-colors hover:text-accent"
              >
                使用 demo 账号快速体验 →
              </button>
            </div>
          </section>
        </main>
      </div>

      <section
        aria-labelledby="capability-story-title"
        className="relative border-t border-line bg-surface"
      >
        <div className="mx-auto max-w-6xl px-6 py-20 lg:px-10 lg:py-24">
          <div className="max-w-2xl">
            <p className="font-mono text-xs font-medium uppercase tracking-[0.2em] text-accent">
              From question to evidence
            </p>
            <h2
              id="capability-story-title"
              className="mt-4 font-display text-3xl font-medium tracking-tight text-ink sm:text-4xl"
            >
              不只给答案，也交付可信的分析过程
            </h2>
            <p className="mt-4 leading-relaxed text-muted">
              从理解业务问题，到安全执行，再到可核验交付，每一步都保留上下文与边界。
            </p>
          </div>

          <ol className="mt-14 grid border-y border-line md:grid-cols-3">
            {CAPABILITY_STORY.map((item, index) => (
              <li
                key={item.title}
                className={`py-8 md:min-h-64 md:py-9 ${
                  index > 0 ? 'border-t border-line md:border-l md:border-t-0 md:pl-8' : ''
                } ${index < CAPABILITY_STORY.length - 1 ? 'md:pr-8' : ''}`}
              >
                <div className="flex items-baseline justify-between gap-4">
                  <h3 className="font-display text-2xl font-medium text-ink">{item.title}</h3>
                  <span className="font-mono text-xs text-accent">{item.step}</span>
                </div>
                <p className="mt-8 font-mono text-xs leading-6 text-accent">{item.summary}</p>
                <p className="mt-4 text-sm leading-7 text-muted">{item.detail}</p>
              </li>
            ))}
          </ol>
        </div>
      </section>
    </div>
  )
}
