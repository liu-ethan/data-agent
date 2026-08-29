export const TOKEN_KEY = 'da.access_token'

export type TokenPayload = {
  exp?: number
  sub?: string
  role?: string
  username?: string
}

export function readToken(): string | undefined {
  return localStorage.getItem(TOKEN_KEY) || undefined
}

export function writeToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token)
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY)
}

export function tokenPayload(token: string): TokenPayload | undefined {
  const parts = token.split('.')
  if (parts.length < 2) return undefined
  try {
    const normalized = parts[1].replace(/-/g, '+').replace(/_/g, '/')
    const padded = normalized.padEnd(normalized.length + ((4 - (normalized.length % 4)) % 4), '=')
    return JSON.parse(atob(padded)) as TokenPayload
  } catch {
    return undefined
  }
}

export function isTokenExpired(token: string): boolean {
  const payload = tokenPayload(token)
  if (!payload?.exp) return true
  return payload.exp * 1000 <= Date.now()
}

export function restoreToken(): string | undefined {
  const token = readToken()
  if (!token || isTokenExpired(token)) {
    clearToken()
    return undefined
  }
  return token
}
