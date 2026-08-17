import type {RegisterRequest, RecommendedQuestionsResponse, StreamEvent} from './types'

export const API = import.meta.env.VITE_API_BASE_URL ?? ''

export function newClientRequestId(): string {
  const randomUUID = globalThis.crypto?.randomUUID
  if (typeof randomUUID === 'function') return randomUUID.call(globalThis.crypto)
  return `web-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`
}

export const ERROR_GUIDANCE: Record<string, string> = {
  AUTH_REQUIRED: '请重新登录后继续。',
  AUTH_INVALID: '登录已失效，请重新登录。',
  AUTH_INVALID_CREDENTIALS: '账号或密码不正确，请确认后重试。',
  ACCOUNT_TAKEN: '该账号已被注册，请更换账号或直接登录。',
  INVITE_INVALID: '邀请码无效、已用尽或已过期，请联系管理员。',
  PERMISSION_DENIED: '当前身份无权访问该数据，请调整范围或联系管理员。',
  QUERY_TIMEOUT: '查询超时，请缩短时间范围或减少维度。',
  QUERY_TOO_EXPENSIVE: '查询范围过大，请减少表、维度或时间跨度。',
  REJECTED: '当前条件无法执行该查询，请修改时间范围、维度或筛选后重试。',
  CHECKPOINT_CONFLICT: '线程已在其他窗口更新，请重新打开线程后再试。',
  ARTIFACT_STALE: '结果制品已过期或权限发生变化，请重新运行查询。',
  INTERRUPT_INVALID: '该澄清请求已失效，请重新打开线程。',
}

export class ApiError extends Error {
  status?: number
  constructor(public code: string, public traceId?: string, status?: number) {
    super(`${ERROR_GUIDANCE[code] ?? code}${traceId ? ` Trace: ${traceId}` : ''}`)
    this.name = 'ApiError'
    this.status = status
  }
}

export async function requestJson<T>(path: string, token?: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (!headers.has('X-Request-ID')) headers.set('X-Request-ID', newClientRequestId())
  const response = await fetch(`${API}${path}`, {...init, headers})
  const payload = await response.json().catch(() => ({detail: 'REQUEST_FAILED'}))
  if (!response.ok) {
    const code = String(payload.error_code ?? payload.detail ?? 'REQUEST_FAILED')
    throw new ApiError(code, payload.trace_id, response.status)
  }
  return payload as T
}

export function tokenExpiry(token: string): Date | undefined {
  try {
    const part = token.split('.')[1]
    if (!part) return
    const normalized = part.replace(/-/g, '+').replace(/_/g, '/')
    const payload = JSON.parse(atob(normalized)) as {exp?: number}
    return payload.exp ? new Date(payload.exp * 1000) : undefined
  } catch {
    return undefined
  }
}

export function isChartDsl(value: unknown): value is import('./types').ChartDsl {
  if (!value || typeof value !== 'object') return false
  const item = value as Record<string, unknown>
  return ['bar', 'line', 'horizontal_bar'].includes(String(item.type))
    && typeof item.result_id === 'string'
    && typeof item.category_field === 'string'
    && typeof item.value_field === 'string'
}

export async function requestRegister(payload: RegisterRequest): Promise<import('./types').RegistrationResponse> {
  return requestJson('/api/auth/register', undefined, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload),
  })
}

export async function requestRecommendedQuestions(): Promise<string[]> {
  const payload = await requestJson<RecommendedQuestionsResponse>('/api/recommended_questions')
  return payload.items ?? []
}

export async function consumeSse(args: {
  url: string
  token: string
  requestId: string
  cursor: {lastEventId: number}
  signal: AbortSignal
  onEvent: (event: StreamEvent) => void | Promise<void>
  method?: 'GET' | 'POST'
  body?: unknown
}): Promise<void> {
  const headers: Record<string, string> = {
    Authorization: `Bearer ${args.token}`,
    'X-Request-ID': args.requestId,
  }
  if (args.cursor.lastEventId) headers['Last-Event-ID'] = String(args.cursor.lastEventId)
  if (args.method === 'POST') headers['Content-Type'] = 'application/json'
  const response = await fetch(args.url, {
    method: args.method ?? 'GET',
    headers,
    signal: args.signal,
    body: args.method === 'POST' ? JSON.stringify(args.body ?? {}) : undefined,
  })
  if (!response.ok || !response.body) {
    const payload = await response.json().catch(() => ({
      detail: response.status === 401 ? 'AUTH_INVALID' : 'STREAM_UNAVAILABLE',
    }))
    throw new ApiError(String(payload.error_code ?? payload.detail ?? 'STREAM_UNAVAILABLE'), payload.trace_id, response.status)
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let terminal = false

  async function processPacket(packet: string) {
    if (!packet || packet.startsWith(':')) return
    const lines = packet.split('\n')
    const eventId = lines.find(line => line.startsWith('id:'))?.slice(3).trim()
    const name = lines.find(line => line.startsWith('event:'))?.slice(6).trim()
    const data = lines.filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n')
    if (!name || !data) return
    const parsed = JSON.parse(data) as StreamEvent
    parsed.event = name as StreamEvent['event']
    if (eventId) {
      parsed.eventId = Number(eventId)
      args.cursor.lastEventId = parsed.eventId
    }
    terminal = ['run.completed', 'run.failed', 'interrupt.created'].includes(name)
    await args.onEvent(parsed)
  }

  for (;;) {
    const {done, value} = await reader.read()
    if (done) {
      if (buffer.trim()) await processPacket(buffer.replace(/\r\n/g, '\n').trimEnd())
      break
    }
    buffer += decoder.decode(value, {stream: true})
    const packets = buffer.replace(/\r\n/g, '\n').split('\n\n')
    buffer = packets.pop() ?? ''
    for (const packet of packets) await processPacket(packet)
  }
  if (!terminal) throw new TypeError('实时连接在完成事件前中断')
}
