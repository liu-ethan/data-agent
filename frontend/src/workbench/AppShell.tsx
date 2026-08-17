import type {ReactNode} from 'react'
import type {Identity} from '../types'

type Connection = 'ready' | 'running' | 'reconnecting' | 'offline'

const CONNECTION_LABEL: Record<Connection, string> = {
  ready: '已连接',
  running: '正在执行',
  reconnecting: '正在重连',
  offline: '连接已中断',
}

export function AppShell({
  identity,
  connection,
  threadTitle,
  children,
  threadDrawerOpen,
  evidenceDrawerOpen,
  onToggleThreads,
  onToggleEvidence,
  onCloseDrawers,
  onSettings,
  onLogout,
}: {
  identity?: Identity
  connection: Connection
  threadTitle?: string
  children: ReactNode
  threadDrawerOpen: boolean
  evidenceDrawerOpen: boolean
  onToggleThreads: () => void
  onToggleEvidence: () => void
  onCloseDrawers: () => void
  onSettings: () => void
  onLogout: () => void
}) {
  return (
    <div
      className={`workbench${threadDrawerOpen ? ' threads-open' : ''}${evidenceDrawerOpen ? ' evidence-open' : ''}`}
    >
      <header className="workbench-head">
        <div className="brand-lockup">
          <span className="brand-mark" aria-hidden>DR</span>
          <span className="brand-wordmark">Data Runtime</span>
        </div>
        <p className="current-thread" title={threadTitle}>{threadTitle ?? '新问题'}</p>
        <p className={`connection connection-${connection}`} role="status">
          <i aria-hidden />
          {CONNECTION_LABEL[connection]}
        </p>
        <div className="user-menu">
          <span className="user-id" title={identity?.user_id}>{identity?.user_id ?? '—'}</span>
          <span className="user-role">{(identity?.roles ?? []).join('/') || 'USER'}</span>
          <button type="button" onClick={onSettings}>分析设置</button>
          <button type="button" onClick={onLogout}>退出</button>
        </div>
        <div className="drawer-toggles">
          <button type="button" aria-expanded={threadDrawerOpen} onClick={onToggleThreads}>
            会话
          </button>
          <button type="button" aria-expanded={evidenceDrawerOpen} onClick={onToggleEvidence}>
            证据栏
          </button>
        </div>
      </header>
      {(threadDrawerOpen || evidenceDrawerOpen) && (
        <button type="button" className="drawer-mask" aria-label="关闭面板" onClick={onCloseDrawers} />
      )}
      {children}
    </div>
  )
}
