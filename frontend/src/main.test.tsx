import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react'
import {afterEach, describe, expect, it, vi} from 'vitest'
import {Root} from './main'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

function json(body: unknown, status = 200) {
  return Promise.resolve(new Response(JSON.stringify(body), {status, headers: {'Content-Type': 'application/json'}}))
}
function stream(events: Array<{id: number; event: string; data: unknown}>) {
  const body = events.map(item => `id: ${item.id}\nevent: ${item.event}\ndata: ${JSON.stringify(item.data)}\n\n`).join('')
  return Promise.resolve(new Response(new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(body))
      controller.close()
    },
  }), {headers: {'Content-Type': 'text/event-stream'}}))
}
function isStream(url: string, init?: RequestInit) {
  return url.includes('/api/chat/stream') && (init?.method === 'POST' || url.includes('/api/chat/stream?'))
}
function baseFetch(extra: (url: string, init?: RequestInit) => Promise<Response> | undefined) {
  return vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const handled = extra(url, init)
    if (handled) return handled
    if (url.endsWith('/api/auth/login')) return json({access_token: 'test-token'})
    if (url.endsWith('/api/recommended_questions')) return json({items: ['问题 A', '问题 B']})
    if (url.endsWith('/api/me')) return json({user_id: 'u_demo_user', roles: ['USER'], policy_version: 'policy_v2'})
    if (url.endsWith('/api/settings')) return json({values: {timezone: 'Asia/Shanghai'}, schema_version: 'user_preferences_v1'})
    if (url.endsWith('/api/threads')) return json({items: []})
    return json({detail: 'NOT_FOUND'}, 404)
  })
}
function submitLogin() {
  fireEvent.change(screen.getByLabelText('账号'), {target: {value: 'u_demo_user'}})
  fireEvent.change(screen.getByLabelText('密码'), {target: {value: 'test-password'}})
  fireEvent.click(screen.getByRole('button', {name: '登录'}))
}

describe('Data Runtime workbench', () => {
  it('authenticates and loads the governed identity and recommended questions', async () => {
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => {
      const url = String(input)
      if (url.endsWith('/api/auth/login')) return json({access_token: 'test-token'})
      if (url.endsWith('/api/recommended_questions')) return json({items: ['问题 A', '问题 B']})
      if (url.endsWith('/api/me')) return json({user_id: 'u_demo_user', roles: ['USER'], policy_version: 'policy_v2'})
      if (url.endsWith('/api/settings')) return json({values: {timezone: 'Asia/Shanghai'}, schema_version: 'user_preferences_v1'})
      if (url.endsWith('/api/threads')) return json({items: []})
      return json({detail: 'NOT_FOUND'}, 404)
    }))
    render(<Root />)
    submitLogin()
    expect(await screen.findByText(/u_demo_user/)).toBeInTheDocument()
    expect(await screen.findByText('问题 A')).toBeInTheDocument()
    expect(fetch).toHaveBeenCalledWith('/api/me', expect.objectContaining({headers: expect.any(Headers)}))
  })

  it('shows an actionable login error when demo auth is unavailable', async () => {
    vi.stubGlobal('fetch', vi.fn(() => json({detail: 'AUTH_INVALID_CREDENTIALS'}, 401)))
    render(<Root />)
    submitLogin()
    expect(await screen.findByRole('alert')).toHaveTextContent('账号或密码不正确')
  })

  it('reconnects an SSE run at most twice without resubmitting the message', async () => {
    vi.stubGlobal('crypto', {randomUUID: () => 'request-1'})
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/api/auth/login')) return json({access_token: 'test-token'})
      if (url.endsWith('/api/recommended_questions')) return json({items: []})
      if (url.endsWith('/api/me')) return json({user_id: 'u_demo_user', roles: ['USER'], policy_version: 'policy_v2'})
      if (url.endsWith('/api/settings')) return json({values: {timezone: 'Asia/Shanghai'}, schema_version: 'user_preferences_v1'})
      if (url.endsWith('/api/threads')) return json({items: []})
      if (url.includes('/api/chat/stream')) return Promise.reject(new TypeError('network disconnected'))
      return json({detail: 'NOT_FOUND'}, 404)
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<Root />)
    submitLogin()
    await screen.findByText(/u_demo_user/)
    fireEvent.change(screen.getByRole('textbox', {name: '问题'}), {target: {value: '昨天 GMV'}})
    fireEvent.click(screen.getByRole('button', {name: '发送'}))
    expect(await screen.findByRole('alert')).toHaveTextContent(/network disconnected|连接已中断/)
    await waitFor(() => {
      const streamCalls = fetchMock.mock.calls.filter(([input]) => String(input).includes('/api/chat/stream'))
      expect(streamCalls).toHaveLength(3)
    })
    const streamCalls = fetchMock.mock.calls.filter(([input]) => String(input).includes('/api/chat/stream'))
    expect(streamCalls[0][1]).toEqual(expect.objectContaining({method: 'POST'}))
    expect(String(streamCalls[0][0])).not.toContain('昨天')
    expect(streamCalls.slice(1).every(([input, init]) => init?.method === 'GET' && !String(input).includes('昨天'))).toBe(true)
  })

  it('renders incremental evidence, a completed answer and paged result rows', async () => {
    const fetchMock = baseFetch((url, init) => {
      if (isStream(url, init)) {
        return stream([
          {id: 1, event: 'run.started', data: {event: 'run.started', request_id: 'request-complete', thread_id: 'thread-live', status: 'RUNNING'}},
          {id: 2, event: 'node.started', data: {event: 'node.started', request_id: 'request-complete', thread_id: 'thread-live', status: 'RUNNING', node: 'execution_gateway_node', action: 'EXECUTE'}},
          {id: 3, event: 'node.completed', data: {event: 'node.completed', request_id: 'request-complete', thread_id: 'thread-live', status: 'RUNNING', node: 'execution_gateway_node', action: 'EXECUTE', duration_ms: 17}},
          {id: 4, event: 'run.completed', data: {event: 'run.completed', request_id: 'request-complete', thread_id: 'thread-live', status: 'SUCCEEDED', answer: '华东 GMV 为 1280 元。', result_ids: ['result-1'], artifact_ids: [], state_version: 7}},
        ])
      }
      if (url.includes('/api/results/result-1?')) return json({result_id: 'result-1', rows: [{region: '华东', gmv: 1280}], offset: 0, limit: 50, total: 1})
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<Root />)
    submitLogin()
    await screen.findByText(/u_demo_user/)
    fireEvent.change(screen.getByRole('textbox', {name: '问题'}), {target: {value: '昨天华东 GMV'}})
    fireEvent.click(screen.getByRole('button', {name: '发送'}))
    expect(await screen.findByText('华东 GMV 为 1280 元。')).toBeInTheDocument()
    expect(await screen.findByRole('cell', {name: '1280'})).toBeInTheDocument()
    expect(screen.getAllByText(/17 ms/).length).toBeGreaterThan(0)
    expect(screen.getByLabelText('证据栏')).toBeInTheDocument()
  })

  it('shows an interrupt and resumes it with one stable idempotency key', async () => {
    let resumeBody: Record<string, unknown> | undefined
    vi.stubGlobal('crypto', {randomUUID: () => 'stable-client-id'})
    const fetchMock = baseFetch((url, init) => {
      if (isStream(url, init)) {
        return stream([{
          id: 1,
          event: 'interrupt.created',
          data: {
            event: 'interrupt.created', request_id: 'request-i', thread_id: 'thread-i', status: 'WAITING_FOR_USER', state_version: 4,
            interrupt: {
              status: 'WAITING_FOR_USER', reason: 'AMBIGUOUS_METRIC', question: '选择退款率口径', candidates: ['金额退款率'],
              resume_node: 'agent_node', checkpoint_id: 'ckpt-1', interrupt_id: 'interrupt-1', expires_at: '2026-08-16T10:15:00Z', schema_version: 'interrupt_v1',
            },
          },
        }])
      }
      if (url.includes('/interrupts/interrupt-1/resume')) {
        resumeBody = JSON.parse(String(init?.body))
        return json({request_id: 'resume-1', thread_id: 'thread-i', status: 'SUCCEEDED', answer: '已按金额退款率计算。', result_ids: [], artifact_ids: [], events: [], state_version: 8})
      }
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<Root />)
    submitLogin()
    await screen.findByText(/u_demo_user/)
    fireEvent.change(screen.getByRole('textbox', {name: '问题'}), {target: {value: '退款率'}})
    fireEvent.click(screen.getByRole('button', {name: '发送'}))
    expect((await screen.findAllByText('选择退款率口径')).length).toBeGreaterThan(0)
    fireEvent.click(screen.getByRole('button', {name: '金额退款率'}))
    expect(await screen.findByText('已按金额退款率计算。')).toBeInTheDocument()
    expect(resumeBody?.client_request_id).toBe('stable-client-id')
  })

  it('turns a timeout event into actionable guidance', async () => {
    vi.stubGlobal('crypto', {randomUUID: () => 'request-timeout'})
    vi.stubGlobal('fetch', baseFetch((url, init) => isStream(url, init)
      ? stream([{id: 1, event: 'run.failed', data: {event: 'run.failed', request_id: 'request-timeout', thread_id: 'thread-timeout', status: 'TIMEOUT', error_code: 'QUERY_TIMEOUT'}}])
      : undefined))
    render(<Root />)
    submitLogin()
    await screen.findByText(/u_demo_user/)
    fireEvent.change(screen.getByRole('textbox', {name: '问题'}), {target: {value: '查十年数据'}})
    fireEvent.click(screen.getByRole('button', {name: '发送'}))
    expect(await screen.findByRole('alert')).toHaveTextContent('缩短时间范围或减少维度')
  })

  it('does not reveal denied object names on PERMISSION_DENIED', async () => {
    vi.stubGlobal('crypto', {randomUUID: () => 'request-denied'})
    vi.stubGlobal('fetch', baseFetch((url, init) => isStream(url, init)
      ? stream([{
        id: 1,
        event: 'run.failed',
        data: {
          event: 'run.failed', request_id: 'request-denied', thread_id: 'thread-denied', status: 'FAILED',
          error_code: 'PERMISSION_DENIED', answer: '无权访问 orders.secret_cost',
        },
      }])
      : undefined))
    render(<Root />)
    submitLogin()
    await screen.findByText(/u_demo_user/)
    fireEvent.change(screen.getByRole('textbox', {name: '问题'}), {target: {value: '看成本'}})
    fireEvent.click(screen.getByRole('button', {name: '发送'}))
    expect(await screen.findByRole('alert')).toHaveTextContent('当前身份无权访问该数据')
    expect(screen.queryByText(/orders.secret_cost/)).not.toBeInTheDocument()
  })

  it('applies an async-generated thread title in the sidebar', async () => {
    vi.stubGlobal('crypto', {randomUUID: () => 'request-title'})
    const fetchMock = baseFetch((url, init) => {
      if (isStream(url, init)) {
        return stream([
          {id: 1, event: 'run.started', data: {event: 'run.started', request_id: 'request-title', thread_id: 'thread-title', status: 'RUNNING'}},
          {id: 2, event: 'run.completed', data: {event: 'run.completed', request_id: 'request-title', thread_id: 'thread-title', status: 'SUCCEEDED', answer: '完成。', result_ids: [], artifact_ids: [], state_version: 1}},
          {id: 3, event: 'thread.title_updated', data: {event: 'thread.title_updated', request_id: 'request-title', thread_id: 'thread-title', status: 'SUCCEEDED', thread_title: '退款率分析'}},
        ])
      }
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<Root />)
    submitLogin()
    await screen.findByText(/u_demo_user/)
    fireEvent.change(screen.getByRole('textbox', {name: '问题'}), {target: {value: '退款率'}})
    fireEvent.click(screen.getByRole('button', {name: '发送'}))
    expect(await screen.findByText('退款率分析')).toBeInTheDocument()
  })
})
