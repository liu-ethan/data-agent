import { apiFetch } from './client'

export interface SessionSummary {
  id: string
  title: string | null
  updated_at: string
  turn_count: number
}

export interface TurnDisplay {
  columns?: string[]
  rows?: Record<string, unknown>[]
  chart?: {
    type: string
    x: string
    y: string
    title?: string
    series?: string[]
  } | null
  sql_repaired?: boolean
  guardrail_passed?: boolean
  trace?: { event: string; summary: string }[]
}

export interface SessionTurn {
  turn_index: number
  question: string
  intent: string | null
  sql_text: string | null
  result_summary: string | null
  metrics: unknown[]
  time_range: unknown
  filters: Record<string, unknown>
  group_by: unknown[]
  display?: TurnDisplay | null
  created_at: string
}

export async function listSessions(): Promise<SessionSummary[]> {
  const res = await apiFetch('/api/sessions')
  if (!res.ok) throw new Error('加载会话失败')
  const data = await res.json()
  return data.sessions ?? []
}

export async function createSession(): Promise<SessionSummary> {
  const res = await apiFetch('/api/sessions', { method: 'POST' })
  if (!res.ok) throw new Error('创建会话失败')
  return res.json()
}

export async function deleteSession(sessionId: string): Promise<void> {
  const res = await apiFetch(`/api/sessions/${encodeURIComponent(sessionId)}`, {
    method: 'DELETE',
  })
  if (!res.ok) throw new Error('删除会话失败')
}

export async function listSessionTurns(sessionId: string): Promise<SessionTurn[]> {
  const res = await apiFetch(`/api/sessions/${encodeURIComponent(sessionId)}/turns`)
  if (!res.ok) throw new Error('加载会话轮次失败')
  const data = await res.json()
  return data.turns ?? []
}
