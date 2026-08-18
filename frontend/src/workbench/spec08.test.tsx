import {cleanup, fireEvent, render, screen} from '@testing-library/react'
import {afterEach, expect, it, vi} from 'vitest'
import {ChartRenderer} from './ChartRenderer'
import {EMPTY_RESULT_COPY, ResultTable} from './ResultTable'
import {RunEvidenceRail} from './RunEvidenceRail'
import {ThreadList} from './ThreadList'
import type {StreamEvent} from '../types'

afterEach(cleanup)

it('shows a friendly empty state when no threads exist', () => {
  render(<ThreadList threads={[]} onOpen={vi.fn()} onNew={vi.fn()} onDelete={vi.fn()} />)
  expect(screen.getByText(/完成第一次分析后会出现在这里/)).toBeInTheDocument()
})

it('filters threads by search and can start a new session', () => {
  const open = vi.fn()
  const start = vi.fn()
  render(
    <ThreadList
      threads={[
        {thread_id: 't1', title: 'GMV 分析', updated_at: new Date().toISOString()},
        {thread_id: 't2', title: '退款率', updated_at: new Date().toISOString()},
      ]}
      current="t1"
      onOpen={open}
      onNew={start}
      onDelete={vi.fn()}
    />,
  )
  fireEvent.change(screen.getByRole('textbox', {name: '搜索线程'}), {target: {value: 'GMV'}})
  expect(screen.getByRole('button', {name: 'GMV 分析'})).toBeInTheDocument()
  expect(screen.queryByRole('button', {name: '退款率'})).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', {name: /新建问题/}))
  expect(start).toHaveBeenCalled()
})

it('deletes a thread without opening it', () => {
  const open = vi.fn()
  const remove = vi.fn()
  render(
    <ThreadList
      threads={[{thread_id: 't1', title: 'GMV 分析', updated_at: new Date().toISOString()}]}
      onOpen={open}
      onNew={vi.fn()}
      onDelete={remove}
    />,
  )
  fireEvent.click(screen.getByRole('button', {name: '删除 GMV 分析'}))
  expect(remove).toHaveBeenCalledWith('t1')
  expect(open).not.toHaveBeenCalled()
})

it('renders the public evidence spine without hidden prompts', () => {
  const events: StreamEvent[] = [
    {event: 'node.started', request_id: 'r', thread_id: 't', status: 'RUNNING', action: 'EXECUTE', schema_version: 'runtime_event_v1'},
    {event: 'node.completed', request_id: 'r', thread_id: 't', status: 'RUNNING', action: 'EXECUTE', duration_ms: 17, schema_version: 'runtime_event_v1'},
  ]
  render(<RunEvidenceRail events={events} />)
  expect(screen.getByLabelText('证据栏')).toBeInTheDocument()
  expect(screen.getByText('RETRIEVE')).toBeInTheDocument()
  expect(screen.getByText('GENERATE')).toBeInTheDocument()
  expect(screen.getByText('EXECUTE')).toBeInTheDocument()
  expect(screen.getByText('RESPOND')).toBeInTheDocument()
  expect(screen.getByText(/17 ms/)).toBeInTheDocument()
  expect(screen.queryByText('思考过程')).not.toBeInTheDocument()
  expect(screen.queryByText(/prompt/i)).not.toBeInTheDocument()
})

it('shows EMPTY copy instead of a numeric zero', () => {
  render(
    <ResultTable
      result={{result_id: 'r1', rows: [], offset: 0, limit: 50, total: 0}}
      onDownload={vi.fn()}
    />,
  )
  expect(screen.getByText(EMPTY_RESULT_COPY)).toBeInTheDocument()
  expect(screen.queryByText(/0 行/)).not.toBeInTheDocument()
})

it('rejects chart types outside the ECharts DSL whitelist', () => {
  render(
    <ChartRenderer
      dsl={{type: 'scatter' as 'bar', result_id: 'r', category_field: 'x', value_field: 'y'}}
      result={{result_id: 'r', rows: [], offset: 0, limit: 50, total: 0}}
    />,
  )
  expect(screen.getByText(/图表类型不在白名单/)).toBeInTheDocument()
})
