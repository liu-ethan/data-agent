import type {Identity, ThreadSummary} from '../types'

export function Sidebar({identity,threads,current,onOpen,onNew,onLogout,onSettings}:{
  identity?:Identity
  threads:ThreadSummary[]
  current?:string
  onOpen:(id:string)=>void
  onNew:()=>void
  onLogout:()=>void
  onSettings:()=>void
}){
  return <aside className="sidebar" aria-label="会话侧栏">
    <header className="sidebar-brand">
      <span className="brand-mark" aria-hidden>DR</span>
      <span className="brand-wordmark"><em>Data</em> Runtime</span>
    </header>
    <button type="button" className="sidebar-new" onClick={onNew}>
      <span aria-hidden>＋</span> 新建会话
    </button>
    <p className="sidebar-section">最近会话</p>
    <nav className="sidebar-list">
      {threads.length===0
        ? <p className="sidebar-empty">完成第一次分析后会出现在这里。</p>
        : threads.map(thread=>{
            const active=thread.thread_id===current
            return <button key={thread.thread_id} type="button"
              className={`sidebar-item ${active?'active':''}`}
              onClick={()=>onOpen(thread.thread_id)}
              aria-current={active?'page':undefined}>
              <span className="sidebar-item-title">{thread.title}</span>
              <time className="sidebar-item-time">{formatRelative(thread.updated_at)}</time>
            </button>
          })}
    </nav>
    <footer className="sidebar-foot">
      <button type="button" className="sidebar-link" onClick={onSettings}>⚙ 分析设置</button>
      <div className="sidebar-identity" title={identity?.user_id}>
        <span className="sidebar-identity-name">{identity?.user_id}</span>
        <span className="sidebar-identity-role">{(identity?.roles??[]).join('/')||'—'}</span>
      </div>
      <button type="button" className="sidebar-link" onClick={onLogout}>⏏ 退出</button>
    </footer>
  </aside>
}

function formatRelative(value:string){
  const date=new Date(value)
  if(Number.isNaN(date.getTime()))return value
  const diffMs=Date.now()-date.getTime()
  const minutes=Math.round(diffMs/60_000)
  if(minutes<1)return '刚刚'
  if(minutes<60)return `${minutes} 分钟前`
  const hours=Math.round(minutes/60)
  if(hours<24)return `${hours} 小时前`
  const days=Math.round(hours/24)
  if(days<7)return `${days} 天前`
  return date.toLocaleDateString()
}