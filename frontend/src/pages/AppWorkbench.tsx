import { useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/AuthContext'

// Placeholder workbench — Task 8 will build out the full layout
// (examples, schema list, chat panel, SQL/result view, trace).
export default function AppWorkbench() {
  const navigate = useNavigate()
  const { user, logout } = useAuth()

  function handleLogout() {
    logout()
    navigate('/')
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-bg text-ink">
      <div className="rounded-2xl border border-line bg-surface p-8 text-center shadow-sm">
        <p className="font-display text-2xl">工作台占位页</p>
        <p className="mt-2 text-sm text-muted">
          已登录：{user?.username} · {user?.role}
        </p>
        <button
          type="button"
          onClick={handleLogout}
          className="mt-6 rounded-lg border border-line px-4 py-2 text-sm text-muted transition-colors hover:border-accent hover:text-accent"
        >
          退出登录
        </button>
      </div>
    </div>
  )
}
