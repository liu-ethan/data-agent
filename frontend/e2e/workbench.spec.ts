import {test, expect} from '@playwright/test'

function jwtWithExp(secondsFromNow: number): string {
  const header = Buffer.from(JSON.stringify({alg: 'none'})).toString('base64url')
  const payload = Buffer.from(
    JSON.stringify({exp: Math.floor(Date.now() / 1000) + secondsFromNow, sub: 'u-admin', role: 'operator'}),
  ).toString('base64url')
  return `${header}.${payload}.sig`
}

async function mockApi(page: import('@playwright/test').Page, opts?: {role?: string}) {
  const role = opts?.role ?? 'operator'
  const threads: {thread_id: string; title: string}[] = [
    {thread_id: 'thread-old', title: '昨天各品类 GMV'},
  ]
  const resumeCalls: unknown[] = []
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url())
    const path = url.pathname
    const method = route.request().method()
    if (path === '/api/auth/login' || path === '/api/auth/register') {
      const body = method === 'POST' ? JSON.parse(route.request().postData() || '{}') : {}
      const resolvedRole = body.role === 'analyst' || role === 'analyst' ? 'analyst' : 'operator'
      return route.fulfill({
        json: {
          token: jwtWithExp(86400),
          user_id: 'u-1',
          username: body.username || 'admin',
          role: resolvedRole,
          display_name: body.username || 'admin',
          expires_in: 86400,
        },
      })
    }
    if (path === '/api/auth/me') {
      return route.fulfill({
        json: {
          user_id: 'u-1',
          username: 'admin',
          role,
          display_name: role === 'analyst' ? '分析师用户' : 'admin',
        },
      })
    }
    if (path === '/api/threads' && method === 'GET') {
      return route.fulfill({json: {threads}})
    }
    if (path === '/api/threads' && method === 'POST') {
      const created = {thread_id: 'thread-new', title: '新会话'}
      threads.unshift(created)
      return route.fulfill({json: created})
    }
    if (method === 'DELETE' && path.startsWith('/api/threads/')) {
      const id = decodeURIComponent(path.slice('/api/threads/'.length))
      const idx = threads.findIndex(item => item.thread_id === id)
      if (idx >= 0) threads.splice(idx, 1)
      return route.fulfill({status: 204, body: ''})
    }
    if (path.endsWith('/resume')) {
      resumeCalls.push(JSON.parse(route.request().postData() || '{}'))
      return route.fulfill({
        contentType: 'text/event-stream',
        body: 'event: token\ndata: {"text":"已提交"}\n\nevent: done\ndata: {"answer":"已提交"}\n\n',
      })
    }
    if (path.endsWith('/messages')) {
      const posted = JSON.parse(route.request().postData() || '{}')
      if (String(posted.message).includes('下架')) {
        return route.fulfill({
          contentType: 'text/event-stream',
          body:
            'event: interrupt\ndata: {"kind":"write_preview","operation_id":"op-1","operation_type":"update_sku_status","affected_rows":1,"changes":[{"id":"1","field":"status","from":"on_sale","to":"off_sale"}]}\n\nevent: done\ndata: {"interrupted":true}\n\n',
        })
      }
      if (String(posted.message).includes('对比')) {
        return route.fulfill({
          contentType: 'text/event-stream',
          body:
            'event: interrupt\ndata: {"kind":"clarify","message":"请选择指标","candidates":[{"id":"gmv","label":"GMV"}]}\n\nevent: done\ndata: {"interrupted":true}\n\n',
        })
      }
      return route.fulfill({
        contentType: 'text/event-stream',
        body:
          'event: result_ref\ndata: {"result_id":"r-1"}\n\nevent: token\ndata: {"text":"GMV 为 100"}\n\nevent: done\ndata: {"answer":"GMV 为 100"}\n\n',
      })
    }
    if (path.startsWith('/api/results/r-1') && !path.endsWith('.csv')) {
      return route.fulfill({
        json: {
          result_id: 'r-1',
          row_count: 1,
          columns: ['sku_id', 'gmv'],
          rows: [{sku_id: 's1', gmv: 100}],
          offset: 0,
          limit: 20,
          time_range: {label: '2026-08'},
          data_as_of: '2026-08-28',
          metric_versions: {gmv: 1},
        },
      })
    }
    return route.fulfill({status: 404, json: {detail: path}})
  })
  return {resumeCalls, threads}
}

test('login survives reload instead of returning to the login page', async ({page}) => {
  await mockApi(page)
  await page.goto('/')
  await page.getByLabel('账号').fill('admin')
  await page.getByLabel('密码').fill('admin')
  await page.getByRole('button', {name: '登录'}).click()
  await expect(page.getByRole('heading', {name: '想查哪一块经营数据？'})).toBeVisible()
  await page.reload()
  await expect(page.getByRole('heading', {name: '想查哪一块经营数据？'})).toBeVisible()
  await expect(page.getByRole('tab', {name: '登录'})).toHaveCount(0)
})

test('expired token shows the login page', async ({page}) => {
  await mockApi(page)
  await page.addInitScript(token => localStorage.setItem('da.access_token', token), jwtWithExp(-60))
  await page.goto('/')
  await expect(page.getByRole('tab', {name: '登录'})).toBeVisible()
})

test('register as analyst shows the analyst role', async ({page}) => {
  await mockApi(page, {role: 'analyst'})
  await page.goto('/')
  await page.getByRole('tab', {name: '注册'}).click()
  await page.getByLabel('账号').fill('new-analyst')
  await page.getByLabel('密码', {exact: true}).fill('secret1')
  await page.getByLabel('再次输入密码').fill('secret1')
  await page.getByRole('button', {name: '分析师'}).click()
  await page.getByRole('button', {name: '注册并进入'}).click()
  await expect(page.getByText('分析师', {exact: true})).toBeVisible()
})

test('empty state has exactly three suggested questions and clicking sends one', async ({page}) => {
  await mockApi(page)
  await page.goto('/')
  await page.getByLabel('账号').fill('admin')
  await page.getByLabel('密码').fill('admin')
  await page.getByRole('button', {name: '登录'}).click()
  const chips = page.locator('.chip')
  await expect(chips).toHaveCount(3)
  await chips.nth(0).click()
  await expect(page.getByText('本月 GMV 是多少？')).toBeVisible()
  await expect(page.getByText('GMV 为 100')).toBeVisible()
  await expect(page.getByRole('cell', {name: 's1'})).toBeVisible()
})

test('write preview confirm calls resume', async ({page}) => {
  const api = await mockApi(page)
  await page.goto('/')
  await page.getByLabel('账号').fill('admin')
  await page.getByLabel('密码').fill('admin')
  await page.getByRole('button', {name: '登录'}).click()
  await page.getByPlaceholder('问一句经营数据，或从上方推荐问开始').fill('下架这些 SKU')
  await page.getByRole('button', {name: '发送'}).click()
  await expect(page.getByText('写入预览')).toBeVisible()
  await expect(page.getByText(/1：status on_sale → off_sale/)).toBeVisible()
  await page.getByRole('button', {name: '确认'}).click()
  await expect.poll(() => api.resumeCalls.length).toBe(1)
  expect(api.resumeCalls[0]).toMatchObject({approved: true})
})

test('clarify interrupt shows candidate buttons', async ({page}) => {
  const api = await mockApi(page)
  await page.goto('/')
  await page.getByLabel('账号').fill('admin')
  await page.getByLabel('密码').fill('admin')
  await page.getByRole('button', {name: '登录'}).click()
  await page.getByPlaceholder('问一句经营数据，或从上方推荐问开始').fill('各品类销售对比')
  await page.getByRole('button', {name: '发送'}).click()
  await expect(page.getByText('请选择', {exact: true})).toBeVisible()
  await page.getByRole('button', {name: 'GMV', exact: true}).click()
  await expect.poll(() => api.resumeCalls.length).toBe(1)
  expect(api.resumeCalls[0]).toMatchObject({selected_id: 'gmv'})
})

test('sidebar can delete a recent thread', async ({page}) => {
  await mockApi(page)
  await page.goto('/')
  await page.getByLabel('账号').fill('admin')
  await page.getByLabel('密码').fill('admin')
  await page.getByRole('button', {name: '登录'}).click()
  await expect(page.getByText('昨天各品类 GMV')).toBeVisible()
  page.once('dialog', dialog => dialog.accept())
  await page.getByRole('button', {name: '删除 昨天各品类 GMV'}).click()
  await expect(page.getByText('昨天各品类 GMV')).toHaveCount(0)
})
