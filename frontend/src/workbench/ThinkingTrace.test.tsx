import {render, screen} from '@testing-library/react'
import {describe, expect, it, vi} from 'vitest'
import {ThinkingTrace} from './ThinkingTrace'

describe('ThinkingTrace', () => {
  it('shows the current node while live', () => {
    render(
      <ThinkingTrace
        steps={[
          {node: 'plan', label: '理解意图', text: '正在判断这是查询还是写入。'},
          {node: 'run_query', label: '查数', text: '正在对照指标与表结构。'},
        ]}
        open
        live
        onToggle={() => undefined}
      />,
    )
    expect(screen.getByText('思考中 · 查数')).toBeTruthy()
    expect(screen.getByText('正在对照指标与表结构。')).toBeTruthy()
  })

  it('collapses thinking after the answer and can be reopened', () => {
    const onToggle = vi.fn()
    const {rerender} = render(
      <ThinkingTrace
        steps={[{node: 'plan', label: '理解意图', text: '正在判断这是查询还是写入。'}]}
        open={false}
        live={false}
        onToggle={onToggle}
      />,
    )
    expect(screen.getByText('已思考 1 步')).toBeTruthy()
    expect(screen.queryByText('正在判断这是查询还是写入。')).toBeNull()
    screen.getByRole('button', {name: '已思考 1 步'}).click()
    expect(onToggle).toHaveBeenCalled()
    rerender(
      <ThinkingTrace
        steps={[{node: 'plan', label: '理解意图', text: '正在判断这是查询还是写入。'}]}
        open
        live={false}
        onToggle={onToggle}
      />,
    )
    expect(screen.getByText('正在判断这是查询还是写入。')).toBeTruthy()
  })
})
