import {StrictMode, useEffect, useState} from 'react'
import {createRoot} from 'react-dom/client'
import {AuthPage} from './auth/AuthPage'
import {clearToken, restoreToken} from './auth/session'
import {ApiError, requestJson} from './client'
import type {UserInfo} from './types'
import {AppShell} from './workbench/AppShell'
import './styles.css'

function Root() {
  const [token, setToken] = useState<string | undefined>(() => restoreToken())
  const [user, setUser] = useState<UserInfo | undefined>()

  useEffect(() => {
    if (!token) {
      setUser(undefined)
      return
    }
    void requestJson<UserInfo>('/api/auth/me', token)
      .then(setUser)
      .catch(err => {
        if (err instanceof ApiError && err.status === 401) {
          clearToken()
          setToken(undefined)
        }
      })
  }, [token])

  if (!token) {
    return (
      <AuthPage
        onAuthed={(next, profile) => {
          setToken(next)
          setUser(profile)
        }}
      />
    )
  }

  const profile: UserInfo = user ?? {
    user_id: '',
    username: '',
    role: 'analyst',
    display_name: '…',
  }

  return (
    <AppShell
      token={token}
      user={profile}
      onLogout={() => {
        clearToken()
        setToken(undefined)
      }}
    />
  )
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
)
