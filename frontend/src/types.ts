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
  resolved?: boolean
}

export type ThinkStep = {
  node: string
  label: string
  text: string
}

export type ChatMessage = {
  id: string
  role: 'user' | 'assistant'
  text: string
  result?: ResultPage
  interrupt?: WritePreview
  thinking?: ThinkStep[]
  thinkingOpen?: boolean
}

export type LoginMeta = {
  eyebrow: string
  headline: string
  lead: string
  ticker_caption: string
  ticker: {label: string}[]
  capabilities: {title: string; body: string}[]
}

export type AppMeta = {
  greeting: string
  suggested_questions: string[]
  empty_thread_title: string
  role_labels: Record<string, string>
  login?: LoginMeta
}

export function roleLabel(role: Role, labels?: Record<string, string>): string {
  if (labels?.[role]) return labels[role]
  return role === 'operator' ? '管理员' : '分析师'
}
