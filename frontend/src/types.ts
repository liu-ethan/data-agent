export type Role = 'analyst' | 'operator'

export type UserInfo = {
  user_id: string
  username: string
  role: Role
  display_name: string
}

export type AuthResponse = UserInfo & {
  token: string
  expires_in?: number
}

export type Thread = {
  thread_id: string
  title: string
  created_at?: string
  updated_at?: string
}

export type ResultPage = {
  result_id: string
  row_count: number
  columns: string[]
  rows: Record<string, unknown>[]
  offset: number
  limit: number
  time_range?: {start: string; end: string; grain?: string; label?: string}
  data_as_of?: string
  metric_versions?: Record<string, number>
  schema_version?: number
}

export type HitlOption = {
  id: string
  label: string
  kind?: string
}

export type HitlChange = {
  id: string
  field?: string
  from?: unknown
  to?: unknown
}

export type WritePreview = {
  kind?: string
  message?: string
  error_code?: string
  error_message?: string
  clarify_kind?: string
  query?: string
  status?: string
  candidates?: HitlOption[]
  options?: HitlOption[]
  operation_id?: string
  operation_type?: string
  affected_rows?: number
  changes?: Array<HitlChange | string>
  rows?: Record<string, unknown>[]
  schema_gap?: {missing_concept?: string; purpose?: string}
  ambiguous?: {reason?: string}
}

export type ChatMessage = {
  id: string
  role: 'user' | 'assistant'
  text: string
  result?: ResultPage
  interrupt?: WritePreview
}

export const SUGGESTED_QUESTIONS = ['本月 GMV 是多少？', '各品类销售对比', '本月退款率如何？'] as const

export function roleLabel(role: Role): string {
  return role === 'operator' ? '管理员' : '分析师'
}
