export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function parseJson(res: Response): Promise<unknown> {
  const text = await res.text()
  if (!text) return undefined
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

export async function requestJson<T>(
  path: string,
  token?: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers)
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  const res = await fetch(path, {...init, headers})
  if (res.status === 204) return undefined as T
  if (!res.ok) {
    const body = await parseJson(res)
    const detail =
      typeof body === 'object' && body && 'detail' in body
        ? String((body as {detail: unknown}).detail)
        : res.statusText
    throw new ApiError(res.status, detail)
  }
  return (await parseJson(res)) as T
}

export type SseHandler = (event: string, data: Record<string, unknown>) => void

export async function readSse(
  path: string,
  token: string,
  body: unknown,
  onEvent: SseHandler,
): Promise<void> {
  const res = await fetch(path, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  })
  if (!res.ok || !res.body) {
    throw new ApiError(res.status, await res.text())
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  while (true) {
    const {value, done} = await reader.read()
    if (done) break
    buf += decoder.decode(value, {stream: true})
    const chunks = buf.split('\n\n')
    buf = chunks.pop() ?? ''
    for (const chunk of chunks) {
      let event = 'message'
      let data = ''
      for (const line of chunk.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim()
        else if (line.startsWith('data:')) data += line.slice(5).trim()
      }
      if (!data) continue
      try {
        onEvent(event, JSON.parse(data) as Record<string, unknown>)
      } catch {
        onEvent(event, {raw: data})
      }
    }
  }
}
