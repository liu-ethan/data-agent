import {render, screen} from '@testing-library/react'
import {describe, expect, it, vi} from 'vitest'
import {InterruptPanel} from './InterruptPanel'

describe('InterruptPanel', () => {
  it('renders clarify candidates as buttons', () => {
    const onResume = vi.fn()
    render(
      <InterruptPanel
        interrupt={{
          kind: 'clarify',
          message: '请选择指标',
          candidates: [{id: 'gmv', label: 'GMV'}],
        }}
        role="operator"
        onResume={onResume}
      />,
    )
    expect(screen.getByText('请选择')).toBeTruthy()
    expect(screen.getByText('请选择指标')).toBeTruthy()
    screen.getByRole('button', {name: 'GMV'}).click()
    expect(onResume).toHaveBeenCalledWith({selected_id: 'gmv'})
  })

  it('shows query error details instead of an empty confirm box', () => {
    render(
      <InterruptPanel
        interrupt={{
          kind: 'query_error',
          error_code: 'SCHEMA_GAP',
          error_message: '缺少仓储表',
          schema_gap: {missing_concept: '仓储表'},
        }}
        role="operator"
        onResume={() => undefined}
      />,
    )
    expect(screen.getByText('查询需要补充')).toBeTruthy()
    expect(screen.getByText('缺少仓储表')).toBeTruthy()
    expect(screen.queryByRole('button', {name: '确认'})).toBeNull()
  })

  it('shows write preview details and confirm for operator', () => {
    const onResume = vi.fn()
    render(
      <InterruptPanel
        interrupt={{
          kind: 'write_preview',
          operation_id: 'op-1',
          operation_type: 'update_sku_status',
          affected_rows: 2,
          changes: [{id: '1', field: 'status', from: 'on_sale', to: 'off_sale'}],
        }}
        role="operator"
        onResume={onResume}
      />,
    )
    expect(screen.getByText('写入预览')).toBeTruthy()
    expect(screen.getByText(/update_sku_status/)).toBeTruthy()
    expect(screen.getByText(/1：status on_sale → off_sale/)).toBeTruthy()
    screen.getByRole('button', {name: '确认'}).click()
    expect(onResume).toHaveBeenCalledWith({approved: true})
  })
})
