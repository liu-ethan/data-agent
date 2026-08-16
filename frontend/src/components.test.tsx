import {cleanup,fireEvent,render,screen} from '@testing-library/react'
import {afterEach,expect,it,vi} from 'vitest'
import {Sidebar} from './dashboard/Sidebar'

afterEach(cleanup)

it('shows a friendly empty state in the sidebar when no threads exist',()=>{
  render(<Sidebar threads={[]} onOpen={vi.fn()} onNew={vi.fn()} onLogout={vi.fn()} onSettings={vi.fn()}/>)
  expect(screen.getByText(/完成第一次分析后会出现在这里/)).toBeInTheDocument()
})

it('opens a thread and starts a new session from the sidebar',()=>{
  const open=vi.fn(),start=vi.fn()
  render(<Sidebar identity={{user_id:'u_demo_user',roles:['USER'],policy_version:'policy_v2'}} threads={[{thread_id:'t1',title:'GMV 分析',updated_at:new Date().toISOString()}]} current="t1" onOpen={open} onNew={start} onLogout={vi.fn()} onSettings={vi.fn()}/>)
  fireEvent.click(screen.getByRole('button',{name:/GMV 分析/}))
  expect(open).toHaveBeenCalledWith('t1')
  fireEvent.click(screen.getByRole('button',{name:/新建会话/}))
  expect(start).toHaveBeenCalled()
})