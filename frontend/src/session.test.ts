import {afterEach, expect, it} from 'vitest'
import {clearToken, isTokenExpired, readToken, restoreToken, writeToken} from './auth/session'

function jwtWithExp(secondsFromNow: number): string {
  const header = btoa(JSON.stringify({alg: 'none'}))
  const payload = btoa(
    JSON.stringify({exp: Math.floor(Date.now() / 1000) + secondsFromNow, sub: 'u-admin'}),
  )
  return `${header}.${payload}.sig`
}

afterEach(() => {
  clearToken()
})

it('restores an unexpired stored access token', () => {
  const token = jwtWithExp(3600)
  writeToken(token)
  expect(readToken()).toBe(token)
  expect(restoreToken()).toBe(token)
})

it('drops an expired access token so refresh cannot keep a dead session', () => {
  writeToken(jwtWithExp(-60))
  expect(isTokenExpired(readToken()!)).toBe(true)
  expect(restoreToken()).toBeUndefined()
  expect(readToken()).toBeUndefined()
})

it('treats an opaque token without exp as expired', () => {
  writeToken('not-a-jwt')
  expect(restoreToken()).toBeUndefined()
})
