import {afterEach, expect, it, vi} from 'vitest'
import {
  consumeSse, newClientRequestId, readStoredAccessToken, tokenExpiry,
  writeStoredAccessToken,
} from './client'
import type {StreamEvent} from './types'

it('replays SSE after Last-Event-ID without dropping repeated graph nodes', async () => {
  const payload = [
    `id: 8\nevent: node.started\ndata: {"event":"node.started","request_id":"r","thread_id":"t","status":"RUNNING","node":"agent_node","action":"RETRIEVE"}\n\n`,
    `id: 9\nevent: node.started\ndata: {"event":"node.started","request_id":"r","thread_id":"t","status":"RUNNING","node":"agent_node","action":"GENERATE"}\n\n`,
    `id: 10\nevent: run.completed\ndata: {"event":"run.completed","request_id":"r","thread_id":"t","status":"SUCCEEDED","result_ids":[],"artifact_ids":[]}\n\n`,
  ].join('')
  const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => Promise.resolve(new Response(new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(payload))
      controller.close()
    },
  }), {status: 200})))
  vi.stubGlobal('fetch', fetchMock)
  const cursor = {lastEventId: 7}
  const events: number[] = []
  await consumeSse({
    url: '/api/chat/stream?request_id=r',
    token: 'token',
    requestId: 'r',
    cursor,
    signal: new AbortController().signal,
    onEvent: event => { events.push(event.eventId!) },
  })
  expect(events).toEqual([8, 9, 10])
  expect(cursor.lastEventId).toBe(10)
  expect(fetchMock).toHaveBeenCalledWith('/api/chat/stream?request_id=r', expect.objectContaining({
    headers: expect.objectContaining({'Last-Event-ID': '7'}),
  }))
})

it('treats interrupt.created as terminal even when later node events follow', async () => {
  const payload = [
    `id: 1\nevent: interrupt.created\ndata: {"event":"interrupt.created","request_id":"r","thread_id":"t","status":"WAITING_FOR_USER"}\n\n`,
    `id: 2\nevent: node.completed\ndata: {"event":"node.completed","request_id":"r","thread_id":"t","status":"WAITING_FOR_USER","node":"execution_gateway_node","action":"EXECUTE","duration_ms":65}\n\n`,
    `id: 3\nevent: node.started\ndata: {"event":"node.started","request_id":"r","thread_id":"t","status":"WAITING_FOR_USER","node":"agent_node","action":"ASK_USER"}\n\n`,
    `id: 4\nevent: node.completed\ndata: {"event":"node.completed","request_id":"r","thread_id":"t","status":"WAITING_FOR_USER","node":"agent_node","action":"ASK_USER","duration_ms":3}\n\n`,
  ].join('')
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response(new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(payload))
      controller.close()
    },
  }), {status: 200}))))
  const events: string[] = []
  await consumeSse({
    url: '/api/chat/stream',
    token: 'token',
    requestId: 'r',
    cursor: {lastEventId: 0},
    signal: new AbortController().signal,
    onEvent: event => { events.push(event.event) },
  })
  expect(events).toEqual([
    'interrupt.created',
    'node.completed',
    'node.started',
    'node.completed',
  ])
})

it('processes a terminal SSE packet even when the connection omits the final blank line', async () => {
  const fetchMock = vi.fn(() => Promise.resolve(new Response(new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(
        'id: 1\nevent: run.completed\ndata: {"event":"run.completed","request_id":"r","thread_id":"t","status":"SUCCEEDED","result_ids":[],"artifact_ids":[]}',
      ))
      controller.close()
    },
  }))))
  vi.stubGlobal('fetch', fetchMock)
  const events: StreamEvent[] = []
  await consumeSse({
    url: '/api/chat/stream',
    token: 'token',
    requestId: 'r',
    cursor: {lastEventId: 0},
    signal: new AbortController().signal,
    onEvent: event => { events.push(event) },
  })
  expect(events).toHaveLength(1)
  expect(events[0].event).toBe('run.completed')
})

it('creates request ids when randomUUID is unavailable on a LAN HTTP origin', () => {
  vi.stubGlobal('crypto', {})
  const first = newClientRequestId()
  const second = newClientRequestId()
  expect(first).toMatch(/^web-/)
  expect(second).not.toBe(first)
})

afterEach(() => {
  localStorage.clear()
})

function jwtWithExp(secondsFromNow: number) {
  const exp = Math.floor(Date.now() / 1000) + secondsFromNow
  return `eyJhbGciOiJub25lIn0.${btoa(JSON.stringify({exp}))}.sig`
}

it('restores an unexpired stored access token and drops an expired one', () => {
  const fresh = jwtWithExp(3600)
  writeStoredAccessToken(fresh)
  expect(readStoredAccessToken()).toBe(fresh)
  expect(tokenExpiry(fresh)?.getTime()).toBeGreaterThan(Date.now())
  writeStoredAccessToken(jwtWithExp(-60))
  expect(readStoredAccessToken()).toBeUndefined()
  expect(localStorage.getItem('dra.access_token')).toBeNull()
})
