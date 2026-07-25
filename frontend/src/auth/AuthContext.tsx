import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from 'react'
import { apiFetch } from '../api/client'
import { clearToken, getToken, setToken } from './token'

export type UserRole = 'analyst' | 'admin'

export interface AuthUser {
  id: string
  username: string
  role: UserRole
}

interface AuthResponse {
  access_token: string
  token_type: string
  user: AuthUser
}

interface AuthContextValue {
  user: AuthUser | null
  loading: boolean
  login: (username: string, password: string) => Promise<void>
  register: (
    username: string,
    password: string,
    role: UserRole,
    inviteCode?: string,
  ) => Promise<void>
  logout: () => void
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined)

async function extractErrorMessage(res: Response): Promise<string> {
  try {
    const data = await res.json()
    if (typeof data.detail === 'string') return data.detail
  } catch {
    // ignore body parse errors
  }
  return '请求失败，请稍后重试'
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const token = getToken()
    if (!token) {
      setLoading(false)
      return
    }
    apiFetch('/api/auth/me')
      .then(async (res) => {
        if (res.ok) {
          setUser(await res.json())
        } else {
          clearToken()
        }
      })
      .catch(() => clearToken())
      .finally(() => setLoading(false))
  }, [])

  const login = useCallback(async (username: string, password: string) => {
    const res = await apiFetch('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    })
    if (!res.ok) throw new Error(await extractErrorMessage(res))
    const data: AuthResponse = await res.json()
    setToken(data.access_token)
    setUser(data.user)
  }, [])

  const register = useCallback(
    async (username: string, password: string, role: UserRole, inviteCode?: string) => {
      const res = await apiFetch('/api/auth/register', {
        method: 'POST',
        body: JSON.stringify({
          username,
          password,
          role,
          invite_code: inviteCode || null,
        }),
      })
      if (!res.ok) throw new Error(await extractErrorMessage(res))
      const data: AuthResponse = await res.json()
      setToken(data.access_token)
      setUser(data.user)
    },
    [],
  )

  const logout = useCallback(() => {
    clearToken()
    setUser(null)
  }, [])

  return (
    <AuthContext.Provider value={{ user, loading, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within an AuthProvider')
  return ctx
}
