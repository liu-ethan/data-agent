import { appConfig } from '../config'
import { getToken } from '../auth/token'

export type ChatEventHandler = (event: string, data: Record<string, unknown>) => void

export interface StreamChatOptions {
  question: string
  sessionId?: string
  signal?: AbortSignal
  onEvent: ChatEventHandler
}

function parseSseChunk(
  buffer: string,
  onEvent: ChatEventHandler,
): string {
  const parts = buffer.split('\n\n')
  const rest = parts.pop() ?? ''
  for (const block of parts) {
    let event = 'message'
    const dataLines: string[] = []
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) {
        event = line.slice(6).trim()
      } else if (line.startsWith('data:')) {
        dataLines.push(line.slice(5).trim())
      }
    }
    if (dataLines.length === 0) continue
    const raw = dataLines.join('\n')
    try {
      const data = JSON.parse(raw) as Record<string, unknown>
      onEvent(event, data)
    } catch {
      onEvent(event, { raw })
    }
  }
  return rest
}

export async function streamChat({
  question,
  sessionId = 'default-anon',
  signal,
  onEvent,
}: StreamChatOptions): Promise<void> {
  const token = getToken()
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    Accept: 'text/event-stream',
  }
  if (token) headers.Authorization = `Bearer ${token}`

  const res = await fetch(`${appConfig.apiBaseUrl}/api/chat`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ question, session_id: sessionId }),
    signal,
  })

  if (!res.ok) {
    let message = `请求失败（${res.status}）`
    try {
      const body = await res.json()
      if (typeof body.detail === 'string') message = body.detail
    } catch {
      // ignore
    }
    throw new Error(message)
  }

  if (!res.body) {
    throw new Error('浏览器不支持流式响应')
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    buffer = parseSseChunk(buffer, onEvent)
  }

  if (buffer.trim()) {
    parseSseChunk(buffer + '\n\n', onEvent)
  }
}
