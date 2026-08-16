import {cleanup,fireEvent,render,screen} from '@testing-library/react'
import {afterEach,expect,it,vi} from 'vitest'
import {ResultTable,ThreadList} from './components'

afterEach(cleanup)

it('distinguishes an empty result from numeric zero and disables paging',()=>{
  render(<ResultTable result={{result_id:'result-empty',rows:[],offset:0,limit:50,total:0}} onPage={vi.fn()} onDownload={vi.fn()}/>)
  expect(screen.getByRole('status')).toHaveTextContent('空结果不等于数值 0')
  expect(screen.getByRole('button',{name:'上一页'})).toBeDisabled()
  expect(screen.getByRole('button',{name:'下一页'})).toBeDisabled()
})

it('filters thread history without changing the selected thread',()=>{
  const open=vi.fn()
  render(<ThreadList threads={[{thread_id:'t1',title:'GMV 分析',updated_at:'2026-08-16T00:00:00Z'},{thread_id:'t2',title:'退款分析',updated_at:'2026-08-16T00:00:00Z'}]} current="t1" query="退款" onQuery={vi.fn()} onOpen={open} onNew={vi.fn()}/>)
  expect(screen.queryByText('GMV 分析')).not.toBeInTheDocument();fireEvent.click(screen.getByRole('button',{name:/退款分析/}));expect(open).toHaveBeenCalledWith('t2')
})
