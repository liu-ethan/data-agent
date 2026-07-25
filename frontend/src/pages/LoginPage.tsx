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
        <p className="text-accent">✓ 已生成结果 · {item.ms}ms</p>
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
    <div className="relative min-h-screen overflow-hidden bg-bg">
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
            <h1 className="animate-rise mt-4 font-display text-[2.75rem] font-medium leading-[1.08] tracking-tight text-ink sm:text-[3.5rem]">
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
              秒级返回结论与明细数据表。
            </p>

            <div className="mt-8 max-w-md" style={{ animationDelay: '200ms' }}>
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
    </div>
  )
}
