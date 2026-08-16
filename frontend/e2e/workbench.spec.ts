import {expect,test,type Page} from '@playwright/test'

function sse(items:Array<{id:number;event:string;data:unknown}>){return items.map(item=>`id: ${item.id}\nevent: ${item.event}\ndata: ${JSON.stringify(item.data)}\n\n`).join('')}

async function mockWorkbench(page:Page,mode:'complete'|'interrupt'='complete'){
  await page.route('**/api/**',async route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname
    if(path==='/api/auth/login')return route.fulfill({json:{access_token:'test-token'}})
    if(path==='/api/me')return route.fulfill({json:{user_id:'u_demo_user',roles:['USER'],policy_version:'policy_v2'}})
    if(path==='/api/settings'&&request.method()==='PUT'){const body=request.postDataJSON();return route.fulfill({json:{values:{timezone:body.value},schema_version:'user_preferences_v1'}})}
    if(path==='/api/settings')return route.fulfill({json:{values:{timezone:'Asia/Shanghai'},schema_version:'user_preferences_v1'}})
    if(path==='/api/threads')return route.fulfill({json:{items:[{thread_id:'thread-old',title:'昨天各品类 GMV',updated_at:'2026-08-16T08:00:00Z'}]}})
    if(path==='/api/chat/stream'){
      const events=mode==='interrupt'?[{id:1,event:'interrupt.created',data:{event:'interrupt.created',request_id:'request-1',thread_id:'thread-1',status:'WAITING_FOR_USER',state_version:4,interrupt:{status:'WAITING_FOR_USER',reason:'AMBIGUOUS_METRIC',question:'退款率使用哪个口径？',candidates:['金额退款率'],resume_node:'agent_node',checkpoint_id:'ckpt-1',interrupt_id:'interrupt-1',expires_at:'2026-08-16T10:15:00Z',schema_version:'interrupt_v1'}}}]:[
        {id:1,event:'run.started',data:{event:'run.started',request_id:'request-1',thread_id:'thread-1',status:'RUNNING'}},
        {id:2,event:'node.completed',data:{event:'node.completed',request_id:'request-1',thread_id:'thread-1',status:'RUNNING',node:'execution_gateway_node',action:'EXECUTE',duration_ms:21}},
        {id:3,event:'run.completed',data:{event:'run.completed',request_id:'request-1',thread_id:'thread-1',status:'SUCCEEDED',answer:'查询完成，共 51 行。',result_ids:['result-1'],artifact_ids:[],state_version:7}},
      ]
      return route.fulfill({status:200,contentType:'text/event-stream',body:sse(events)})
    }
    if(path.includes('/interrupts/interrupt-1/resume'))return route.fulfill({json:{request_id:'resume-1',thread_id:'thread-1',status:'SUCCEEDED',answer:'已按金额退款率计算。',result_ids:[],artifact_ids:[],events:[],state_version:8}})
    if(path==='/api/results/result-1'){const offset=Number(url.searchParams.get('offset')??0);return route.fulfill({json:{result_id:'result-1',rows:[{category:offset?'最后一类':'手机',gmv:offset?1:1280}],offset,limit:50,total:51}})}
    return route.fulfill({status:404,json:{detail:'NOT_FOUND'}})
  })
}

async function login(page:Page){await page.goto('/login');await page.getByLabel('账号').fill('u_demo_user');await page.getByLabel('密码').fill('test-password');await page.getByRole('button',{name:'登录'}).click();await expect(page.getByRole('textbox',{name:'问题'})).toBeEditable()}

test('desktop analyst can query, inspect evidence and page governed results',async({page})=>{
  await mockWorkbench(page);await login(page)
  await page.getByRole('textbox',{name:'问题'}).fill('昨天各品类 GMV');await page.getByRole('button',{name:'发送问题'}).click()
  await expect(page.getByText('查询完成，共 51 行。')).toBeVisible();await expect(page.getByRole('cell',{name:'1280'})).toBeVisible();await expect(page.getByRole('complementary',{name:'证据栏'})).toContainText('21 ms')
  await page.getByRole('button',{name:'下一页'}).click();await expect(page.getByRole('cell',{name:'最后一类'})).toBeVisible();await expect(page.getByLabel('第 2 页，共 2 页')).toBeVisible()
  await page.screenshot({path:'test-results/workbench-desktop.png',fullPage:true})
})

test('clarification interrupt resumes exactly from the visible choice',async({page})=>{
  await mockWorkbench(page,'interrupt');await login(page)
  await page.getByRole('textbox',{name:'问题'}).fill('退款率');await page.getByRole('button',{name:'发送问题'}).click();await expect(page.getByText('退款率使用哪个口径？')).toBeVisible();await page.getByRole('button',{name:'金额退款率'}).click();await expect(page.getByText('已按金额退款率计算。')).toBeVisible()
})

test('mobile layout exposes thread and evidence drawers',async({page})=>{
  await page.setViewportSize({width:390,height:844});await mockWorkbench(page);await login(page)
  await page.getByRole('button',{name:'打开证据栏'}).click();await expect(page.getByRole('dialog',{name:'证据抽屉'})).toContainText('运行轨迹');await page.getByRole('button',{name:'关闭抽屉'}).click()
  await page.getByRole('button',{name:'打开线程列表'}).click();await expect(page.getByRole('dialog',{name:'线程抽屉'})).toContainText('昨天各品类 GMV')
  await page.screenshot({path:'test-results/workbench-mobile.png',fullPage:true})
})

test('confirmed timezone preference remains explicit in settings',async({page})=>{
  await mockWorkbench(page);await login(page);await page.getByRole('button',{name:'分析设置'}).click();await page.getByLabel('默认时区').selectOption('UTC');await expect(page.getByLabel('默认时区')).toHaveValue('UTC')
})
