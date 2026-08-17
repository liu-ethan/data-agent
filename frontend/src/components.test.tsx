import {cleanup, fireEvent, render, screen} from '@testing-library/react'
import {afterEach, expect, it, vi} from 'vitest'
import {ThreadList} from './workbench/ThreadList'

afterEach(cleanup)

it('shows a friendly empty state in the thread list when no threads exist', () => {
  render(<ThreadList threads={[]} onOpen={vi.fn()} onNew={vi.fn()} />)
  expect(screen.getByText(/完成第一次分析后会出现在这里/)).toBeInTheDocument()
})

it('opens a thread and starts a new session from the thread list', () => {
  const open = vi.fn()
  const start = vi.fn()
  render(
    <ThreadList
      threads={[{thread_id: 't1', title: 'GMV 分析', updated_at: new Date().toISOString()}]}
      current="t1"
      onOpen={open}
      onNew={start}
    />,
  )
  fireEvent.click(screen.getByRole('button', {name: /GMV 分析/}))
  expect(open).toHaveBeenCalledWith('t1')
  fireEvent.click(screen.getByRole('button', {name: /新建问题/}))
  expect(start).toHaveBeenCalled()
})
