import {useState, type FormEvent} from 'react'
import {requestJson} from '../client'
import {writeToken} from './session'
import type {AuthResponse, Role} from '../types'

type Mode = 'login' | 'register'

export function AuthPage({onAuthed}: {onAuthed: (token: string, user: AuthResponse) => void}) {
  const [mode, setMode] = useState<Mode>('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [role, setRole] = useState<Role>('analyst')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

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

  return (
    <div className="auth-shell">
      <form className="auth-card" onSubmit={submit}>
        <p className="auth-eyebrow">电商问数</p>
        <h1>问数</h1>
        <p className="auth-lead">登录后查询经营指标，管理员可确认写入。</p>
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
                管理员
              </button>
              <button
                type="button"
                className={role === 'analyst' ? 'active' : ''}
                onClick={() => setRole('analyst')}
              >
                分析师
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
  )
}
