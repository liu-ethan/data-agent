import {useState, type FormEvent} from 'react'
import {ApiError, requestJson} from '../client'

type Mode = 'login' | 'register'

export function AuthPage({onAuthenticated}:{onAuthenticated:(token:string)=>void}){
  const[mode,setMode]=useState<Mode>('login')
  return <main className="auth-page">
    <section className="auth-card" aria-label="账号认证">
      <header className="auth-card-head">
        <span className="brand-mark" aria-hidden>DR</span>
        <span className="brand-wordmark"><em>Data</em> Runtime</span>
      </header>
      <h1 className="auth-title">{mode==='login'?'欢迎回来':'创建账号'}</h1>
      <p className="auth-sub">{mode==='login'?'使用应用账号登录，权限范围由服务端重新计算。':'注册 USER 或 ADMIN 都需邀请码，向管理员索取。'}</p>
      <nav className="auth-tabs" role="tablist">
        <button type="button" role="tab" aria-selected={mode==='login'} className={`auth-tab ${mode==='login'?'active':''}`} onClick={()=>setMode('login')}>登录</button>
        <button type="button" role="tab" aria-selected={mode==='register'} className={`auth-tab ${mode==='register'?'active':''}`} onClick={()=>setMode('register')}>注册</button>
      </nav>
      {mode==='login'
        ? <LoginPanel onAuthenticated={onAuthenticated}/>
        : <RegisterPanel onRegistered={()=>setMode('login')}/>}
    </section>
    <aside className="auth-aside" aria-hidden>
      <p className="auth-aside-eyebrow">Evidence-driven analysis</p>
      <h2 className="auth-aside-title">让数据问题，<br/>有<em>据</em>可循。</h2>
      <p className="auth-aside-body">检索目录、生成查询、只读执行、回写证据。每一次回答都附带运行轨迹。</p>
    </aside>
  </main>
}

function LoginPanel({onAuthenticated}:{onAuthenticated:(token:string)=>void}){
  const[account,setAccount]=useState(''),[password,setPassword]=useState(''),[error,setError]=useState<string>(),[loading,setLoading]=useState(false)
  async function submit(event:FormEvent){event.preventDefault();if(!account.trim()||!password){setError('请输入账号和密码。');return}setLoading(true);setError(undefined);try{const payload=await requestJson<{access_token:string}>('/api/auth/login',undefined,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({account:account.trim(),password})});onAuthenticated(payload.access_token)}catch(value){setError(value instanceof ApiError?value.message:'登录失败')}finally{setLoading(false)}}
  return <form className="auth-form" onSubmit={event=>void submit(event)}>
    <label htmlFor="login-account">账号</label>
    <input id="login-account" name="account" autoComplete="username" value={account} onChange={event=>setAccount(event.target.value)}/>
    <label htmlFor="login-password">密码</label>
    <input id="login-password" name="password" type="password" autoComplete="current-password" value={password} onChange={event=>setPassword(event.target.value)}/>
    <button type="submit" className="auth-primary" disabled={loading}>{loading?'正在验证…':'登录'}</button>
    {error&&<p className="auth-error" role="alert">{error}</p>}
  </form>
}

function RegisterPanel({onRegistered}:{onRegistered:()=>void}){
  const[account,setAccount]=useState(''),[password,setPassword]=useState(''),[confirm,setConfirm]=useState(''),[role,setRole]=useState<'USER'|'ADMIN'>('USER'),[invite,setInvite]=useState(''),[error,setError]=useState<string>(),[success,setSuccess]=useState<string>(),[loading,setLoading]=useState(false)
  async function submit(event:FormEvent){event.preventDefault();if(password!==confirm){setError('两次输入的密码不一致。');return}setLoading(true);setError(undefined);setSuccess(undefined);try{const payload=await requestJson<{status:string;account:string;role:string}>('/api/auth/register',undefined,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({account:account.trim(),password,confirm_password:confirm,role,invite_code:invite.trim()})});setSuccess(`账号 ${payload.account} 已创建（${payload.role}），请使用新密码登录。`);setAccount('');setPassword('');setConfirm('');setInvite('');onRegistered()}catch(value){setError(value instanceof ApiError?value.message:'注册失败')}finally{setLoading(false)}}
  return <form className="auth-form" onSubmit={event=>void submit(event)}>
    <label htmlFor="register-account">账号</label>
    <input id="register-account" name="account" autoComplete="username" value={account} onChange={event=>setAccount(event.target.value)} placeholder="3-64 位字母/数字/_-./" />
    <label htmlFor="register-password">密码</label>
    <input id="register-password" name="password" type="password" autoComplete="new-password" value={password} onChange={event=>setPassword(event.target.value)} placeholder="至少 10 位" />
    <label htmlFor="register-confirm">再次输入密码</label>
    <input id="register-confirm" name="confirm" type="password" autoComplete="new-password" value={confirm} onChange={event=>setConfirm(event.target.value)} />
    <fieldset className="role-picker">
      <legend>身份</legend>
      <label><input type="radio" name="role" value="USER" checked={role==='USER'} onChange={()=>setRole('USER')}/>普通用户</label>
      <label><input type="radio" name="role" value="ADMIN" checked={role==='ADMIN'} onChange={()=>setRole('ADMIN')}/>管理员</label>
    </fieldset>
    <label htmlFor="register-invite">邀请码</label>
    <input id="register-invite" name="invite" autoComplete="off" value={invite} onChange={event=>setInvite(event.target.value)} placeholder="向管理员索取"/>
    <button type="submit" className="auth-primary" disabled={loading}>{loading?'正在创建…':'注册并登录'}</button>
    {error&&<p className="auth-error" role="alert">{error}</p>}
    {success&&<p className="auth-success" role="status">{success}</p>}
  </form>
}