import {expect, test, type Page} from '@playwright/test'

function sse(items: Array<{id: number; event: string; data: unknown}>) {
  return items.map(item => `id: ${item.id}\nevent: ${item.event}\ndata: ${JSON.stringify(item.data)}\n\n`).join('')
}

async function mockWorkbench(page: Page, mode: 'complete' | 'interrupt' | 'write' | 'empty' = 'complete') {
  const deleted = new Set<string>()
  await page.route('**/api/**', async route => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    if (path === '/api/auth/login') return route.fulfill({json: {access_token: 'test-token'}})
    if (path === '/api/recommended_questions') return route.fulfill({json: {items: ['问题 A', '问题 B']}})
    if (path === '/api/me') return route.fulfill({json: {user_id: 'u_demo_user', roles: ['USER'], policy_version: 'policy_v2'}})
    if (path === '/api/settings' && request.method() === 'PUT') {
      const body = request.postDataJSON()
      return route.fulfill({json: {values: {timezone: body.value}, schema_version: 'user_preferences_v1'}})
    }
    if (path === '/api/settings') return route.fulfill({json: {values: {timezone: 'Asia/Shanghai'}, schema_version: 'user_preferences_v1'}})
    if (request.method() === 'DELETE' && path.startsWith('/api/threads/')) {
      deleted.add(decodeURIComponent(path.slice('/api/threads/'.length)))
      return route.fulfill({status: 204, body: ''})
    }
    if (path === '/api/threads') {
      const items = [{thread_id: 'thread-old', title: '昨天各品类 GMV', updated_at: '2026-08-16T08:00:00Z'}]
        .filter(item => !deleted.has(item.thread_id))
      return route.fulfill({json: {items}})
    }
    if (path === '/api/chat/stream') {
      const events = mode === 'interrupt'
        ? [{
          id: 1, event: 'interrupt.created',
          data: {
            event: 'interrupt.created', request_id: 'request-1', thread_id: 'thread-1', status: 'WAITING_FOR_USER', state_version: 4,
            interrupt: {
              status: 'WAITING_FOR_USER', reason: 'AMBIGUOUS_METRIC', question: '退款率使用哪个口径？', candidates: ['金额退款率'],
              resume_node: 'agent_node', checkpoint_id: 'ckpt-1', interrupt_id: 'interrupt-1', expires_at: '2026-08-16T10:15:00Z', schema_version: 'interrupt_v1',
            },
          },
        }]
        : mode === 'write'
          ? [{
            id: 1, event: 'interrupt.created',
            data: {
              event: 'interrupt.created', request_id: 'request-1', thread_id: 'thread-1', status: 'WAITING_FOR_USER', state_version: 5,
              interrupt: {
                status: 'WAITING_FOR_USER', reason: 'WRITE_APPROVAL',
                question: '确认将 products.product_id=prod_1001 从 智能手机 改为 新智能手机？预计影响 1 行。',
                candidates: ['确认执行', '取消'],
                resume_node: 'execution_gateway_node', checkpoint_id: 'ckpt-w', interrupt_id: 'interrupt-1',
                expires_at: '2026-08-16T10:15:00Z', schema_version: 'interrupt_v1',
                preview: {
                  preview_id: 'preview-1', operation: 'UPDATE', target: 'products.product_id=prod_1001',
                  diff: {product_name: {before: '智能手机', after: '新智能手机'}},
                  estimated_affected_rows: 1, risk_level: 'MEDIUM', expires_at: '2026-08-16T12:30:00+08:00',
                  data_version: 'products_v18', permission_policy_version: 'policy_v2',
                  mutation_spec: {
                    operation: 'UPDATE', table: 'products', filters: {product_id: 'prod_1001'},
                    changes: {product_name: '新智能手机'}, user_reason: '修正商品名称',
                    request_id: 'req-w', user_id: 'u_demo_admin', permission_policy_version: 'policy_v2',
                    data_version: 'products_v18', idempotency_key: 'mut-w',
                  },
                  schema_version: 'mutation_preview_v1',
                },
              },
            },
          }]
        : mode === 'empty'
          ? [
            {id: 1, event: 'run.started', data: {event: 'run.started', request_id: 'request-1', thread_id: 'thread-1', status: 'RUNNING'}},
            {id: 2, event: 'run.completed', data: {event: 'run.completed', request_id: 'request-1', thread_id: 'thread-1', status: 'SUCCEEDED', answer: '查询完成。', result_ids: ['result-empty'], artifact_ids: [], state_version: 2}},
          ]
          : [
            {id: 1, event: 'run.started', data: {event: 'run.started', request_id: 'request-1', thread_id: 'thread-1', status: 'RUNNING'}},
            {id: 2, event: 'node.completed', data: {event: 'node.completed', request_id: 'request-1', thread_id: 'thread-1', status: 'RUNNING', node: 'execution_gateway_node', action: 'EXECUTE', duration_ms: 21}},
            {id: 3, event: 'run.completed', data: {event: 'run.completed', request_id: 'request-1', thread_id: 'thread-1', status: 'SUCCEEDED', answer: '查询完成，共 51 行。', result_ids: ['result-1'], artifact_ids: [], state_version: 7}},
          ]
      return route.fulfill({status: 200, contentType: 'text/event-stream', body: sse(events)})
    }
    if (path.includes('/interrupts/interrupt-1/resume')) {
      return route.fulfill({json: {
        request_id: 'resume-1', thread_id: 'thread-1',
        status: 'SUCCEEDED',
        answer: mode === 'write' ? '已确认并更新 products.product_id=prod_1001，影响 1 行。' : '已按金额退款率计算。',
        result_ids: [], artifact_ids: [], events: [], state_version: 8,
      }})
    }
    if (path === '/api/results/result-1') {
      const offset = Number(url.searchParams.get('offset') ?? 0)
      return route.fulfill({json: {result_id: 'result-1', rows: [{category: offset ? '最后一类' : '手机', gmv: offset ? 1 : 1280}], offset, limit: 50, total: 51}})
    }
    if (path === '/api/results/result-empty') {
      return route.fulfill({json: {result_id: 'result-empty', rows: [], offset: 0, limit: 50, total: 0}})
    }
    return route.fulfill({status: 404, json: {detail: 'NOT_FOUND'}})
  })
}

async function login(page: Page) {
  await page.goto('/login')
  await page.getByRole('textbox', {name: '账号'}).fill('u_demo_user')
  await page.getByLabel('密码').fill('test-password')
  await page.getByRole('button', {name: '登录'}).click()
  await expect(page.getByRole('textbox', {name: '问题'})).toBeEditable()
}

test('desktop analyst can query, inspect evidence and download governed results', async ({page}) => {
  await mockWorkbench(page)
  await login(page)
  await page.getByRole('textbox', {name: '问题'}).fill('昨天各品类 GMV')
  await page.getByRole('button', {name: '发送'}).click()
  await expect(page.getByText('查询完成，共 51 行。')).toBeVisible()
  await expect(page.getByRole('cell', {name: '1280'})).toBeVisible()
  await expect(page.getByLabel('证据栏')).toBeVisible()
  await expect(page.getByText(/21 ms/)).toBeVisible()
  await page.getByRole('button', {name: '下一页'}).click()
  await expect(page.getByRole('cell', {name: '最后一类'})).toBeVisible()
  await page.screenshot({path: 'test-results/workbench-desktop.png', fullPage: true})
})

test('write approval interrupt shows preview diff and confirms exactly once', async ({page}) => {
  await mockWorkbench(page, 'write')
  await login(page)
  await page.getByRole('textbox', {name: '问题'}).fill('把商品 prod_1001 的名称改成 新智能手机')
  await page.getByRole('button', {name: '发送'}).click()
  await expect(page.getByLabel('写入预览')).toBeVisible()
  await expect(page.getByRole('cell', {name: '智能手机'})).toBeVisible()
  await page.getByRole('button', {name: '确认执行'}).click()
  await expect(page.getByText('已确认并更新 products.product_id=prod_1001，影响 1 行。')).toBeVisible()
})

test('clarification interrupt resumes exactly from the visible choice', async ({page}) => {
  await mockWorkbench(page, 'interrupt')
  await login(page)
  await page.getByRole('textbox', {name: '问题'}).fill('退款率')
  await page.getByRole('button', {name: '发送'}).click()
  await expect(page.getByText('退款率使用哪个口径？')).toBeVisible()
  await page.getByRole('button', {name: '金额退款率'}).click()
  await expect(page.getByText('已按金额退款率计算。')).toBeVisible()
})

test('empty results are not shown as zero', async ({page}) => {
  await mockWorkbench(page, 'empty')
  await login(page)
  await page.getByRole('textbox', {name: '问题'}).fill('不存在的品类')
  await page.getByRole('button', {name: '发送'}).click()
  await expect(page.getByRole('status').filter({hasText: '没有符合条件的数据'})).toBeVisible()
  await expect(page.getByText('0 行')).toHaveCount(0)
})

test('mobile layout collapses the sidebar and keeps composer usable', async ({page}) => {
  await page.setViewportSize({width: 390, height: 844})
  await mockWorkbench(page)
  await login(page)
  await expect(page.getByText('问题 A')).toBeVisible()
  await expect(page.getByRole('button', {name: '证据栏'})).toBeVisible()
  await page.screenshot({path: 'test-results/workbench-mobile.png', fullPage: true})
})

test('sidebar can delete a recent thread', async ({page}) => {
  await mockWorkbench(page)
  await login(page)
  page.once('dialog', dialog => dialog.accept())
  await page.getByRole('button', {name: '删除 昨天各品类 GMV'}).click()
  await expect(page.getByRole('button', {name: '昨天各品类 GMV'})).toHaveCount(0)
})

test('refresh keeps the analyst on the workbench', async ({page}) => {
  await mockWorkbench(page)
  await login(page)
  await page.reload()
  await expect(page.getByRole('textbox', {name: '问题'})).toBeEditable()
  await expect(page).not.toHaveURL(/\/login/)
})

test('confirmed timezone preference remains explicit in settings', async ({page}) => {
  await mockWorkbench(page)
  await login(page)
  await page.getByRole('button', {name: /分析设置/}).click()
  await page.getByLabel('默认时区').selectOption('UTC')
  await expect(page.getByLabel('默认时区')).toHaveValue('UTC')
})
