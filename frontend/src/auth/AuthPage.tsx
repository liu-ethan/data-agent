import {useEffect, useState, type FormEvent} from 'react'
import {requestJson} from '../client'
import {writeToken} from './session'
import type {AppMeta, AuthResponse, LoginMeta, Role} from '../types'

type Mode = 'login' | 'register'

const FALLBACK_LOGIN: LoginMeta = {
  eyebrow: '电商经营分析',
  headline: '用一句话问经营数字',
  lead: '问数对着订单、商品、流量与售后切片作答。分析师查 GMV、品类对比和退款率；改 SKU 状态或库存，必须管理员当场确认。',
  ticker_caption: '可问指标 · 口径已锁定',
  ticker: [
    {label: 'GMV'},
    {label: '实付GMV'},
    {label: '净GMV'},
    {label: '订单量'},
    {label: '客单价'},
    {label: '退款率'},
    {label: '转化率'},
    {label: '新客数'},
    {label: '复购率'},
    {label: '广告ROI'},
  ],
  capabilities: [
    {title: '问得出口径', body: 'GMV、客单价、退款率走已审核公式，模型不能现场编算法。'},
    {title: '查得到对比', body: '按品类、店铺、时间窗取数。缺条件时把可选答案做成按钮，点一下就能继续。'},
    {title: '改得有人点头', body: '下架或调库存先出预览，管理员确认后才写，单次最多 100 行。'},
  ],
}

export function AuthPage({onAuthed}: {onAuthed: (token: string, user: AuthResponse) => void}) {
  const [mode, setMode] = useState<Mode>('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [role, setRole] = useState<Role>('analyst')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [login, setLogin] = useState<LoginMeta>(FALLBACK_LOGIN)
  const [asks, setAsks] = useState<string[]>([
    '本月 GMV 是多少？',
    '各品类销售对比',
    '本月退款率如何？',
  ])
  const [labels, setLabels] = useState<Record<string, string>>({
    operator: '管理员',
    analyst: '分析师',
  })

  useEffect(() => {
    void requestJson<AppMeta>('/api/meta').then(data => {
      if (data.role_labels) setLabels(data.role_labels)
      if (data.suggested_questions?.length) setAsks(data.suggested_questions)
      if (data.login) {
        setLogin({
          ...FALLBACK_LOGIN,
          ...data.login,
          ticker: data.login.ticker?.length ? data.login.ticker : FALLBACK_LOGIN.ticker,
          capabilities: data.login.capabilities?.length
            ? data.login.capabilities
            : FALLBACK_LOGIN.capabilities,
        })
      }
    })
  }, [])

  async function submit(event: FormEvent) {
    event.preventDefault()
    setError('')
    if (mode === 'register' && password !== confirm) {
      setError('两次密码不一致')
      return
    }
    setBusy(true)
    try {
      const path = mode === 'login' ? '/api/auth/login' : '/api/auth/register'
      const payload =
        mode === 'login'
          ? {username, password}
          : {username, password, role}
      const body = await requestJson<AuthResponse>(path, undefined, {
        method: 'POST',
        body: JSON.stringify(payload),
      })
      writeToken(body.token)
      onAuthed(body.token, body)
    } catch (err) {
      setError(err instanceof Error ? err.message : '登录失败')
    } finally {
      setBusy(false)
    }
  }

  const tape = [...login.ticker, ...login.ticker]

  return (
    <div className="auth-stage">
      <div className="auth-stage-inner">
        <section className="auth-pitch">
          <p className="auth-ticker-caption">{login.ticker_caption}</p>
          <div className="auth-ticker" aria-hidden="true">
            <div className="auth-ticker-track">
              {tape.map((item, index) => (
                <span key={`${item.label}-${index}`} className="auth-ticker-item">
                  {item.label}
                </span>
              ))}
            </div>
          </div>
          <p className="auth-eyebrow">{login.eyebrow}</p>
          <h1>{login.headline}</h1>
          <p className="auth-lead">{login.lead}</p>
          <ul className="auth-capabilities">
            {login.capabilities.map(item => (
              <li key={item.title}>
                <strong>{item.title}</strong>
                <span>{item.body}</span>
              </li>
            ))}
          </ul>
          <div className="auth-asks">
            <p className="auth-asks-label">进门就能问</p>
            <div className="auth-asks-row">
              {asks.map(item => (
                <span key={item} className="auth-ask-chip">
                  {item}
                </span>
              ))}
            </div>
          </div>
        </section>
        <form className="auth-card" onSubmit={submit}>
          <p className="auth-card-kicker">工作台</p>
          <h2>进入问数</h2>
          <p className="auth-card-note">分析师只读问数。管理员可确认写入。</p>
          <div className="auth-tabs" role="tablist">
            <button
              type="button"
              role="tab"
              aria-selected={mode === 'login'}
              className={mode === 'login' ? 'active' : ''}
              onClick={() => setMode('login')}
            >
              登录
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={mode === 'register'}
              className={mode === 'register' ? 'active' : ''}
              onClick={() => setMode('register')}
            >
              注册
            </button>
          </div>
          <label htmlFor="username">账号</label>
          <input
            id="username"
            name="username"
            autoComplete="username"
            value={username}
            onChange={event => setUsername(event.target.value)}
          />
          <label htmlFor="password">密码</label>
          <input
            id="password"
            name="password"
            type="password"
            autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
            value={password}
            onChange={event => setPassword(event.target.value)}
          />
          {mode === 'register' ? (
            <>
              <label htmlFor="confirm">再次输入密码</label>
              <input
                id="confirm"
                name="confirm"
                type="password"
                autoComplete="new-password"
                value={confirm}
                onChange={event => setConfirm(event.target.value)}
              />
              <span className="field-label">角色</span>
              <div className="role-pill" role="group" aria-label="选择角色">
                <button
                  type="button"
                  className={role === 'operator' ? 'active' : ''}
                  onClick={() => setRole('operator')}
                >
                  {labels.operator}
                </button>
                <button
                  type="button"
                  className={role === 'analyst' ? 'active' : ''}
                  onClick={() => setRole('analyst')}
                >
                  {labels.analyst}
                </button>
              </div>
            </>
          ) : null}
          {error ? <p className="auth-error">{error}</p> : null}
          <button className="auth-submit" type="submit" disabled={busy}>
            {mode === 'login' ? '登录' : '注册并进入'}
          </button>
        </form>
      </div>
    </div>
  )
}
