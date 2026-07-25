import { appConfig } from '../config'
import { getToken } from '../auth/token'

export async function apiFetch(path: string, init: RequestInit = {}) {
  const headers = new Headers(init.headers)
  headers.set('Content-Type', 'application/json')
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const res = await fetch(`${appConfig.apiBaseUrl}${path}`, { ...init, headers })
  return res
}
