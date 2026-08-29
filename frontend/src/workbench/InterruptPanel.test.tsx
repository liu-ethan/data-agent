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

  it('picks a suggested answer as the next user message', () => {
    const onPick = vi.fn()
    render(
      <InterruptPanel
        interrupt={{
          kind: 'query_error',
          error_code: 'SCHEMA_GAP',
          error_message: 'dim_category.category_name',
          message: '想看各品类的哪项数据？',
          schema_gap: {missing_concept: 'dim_category.category_name'},
          candidates: [{id: 'gmv', label: '各品类 GMV'}],
        }}
        role="operator"
        onResume={() => undefined}
        onPick={onPick}
      />,
    )
    expect(screen.getByText('请选择')).toBeTruthy()
    expect(screen.getByText('想看各品类的哪项数据？')).toBeTruthy()
    expect(screen.queryByText('SCHEMA_GAP')).toBeNull()
    expect(screen.queryByText('dim_category.category_name')).toBeNull()
    screen.getByRole('button', {name: '各品类 GMV'}).click()
    expect(onPick).toHaveBeenCalledWith('各品类 GMV')
  })

  it('hides gateway allowlist English from the user', () => {
    render(
      <InterruptPanel
        interrupt={{
          kind: 'query_error',
          error_code: 'UNSAFE_SQL',
          error_message: 'column is not in the task allowlist',
          message: '这个问法没法安全查到数据，选一个继续：',
          candidates: [{id: 'gmv', label: '本月 GMV 是多少？'}],
        }}
        role="operator"
        onResume={() => undefined}
        onPick={() => undefined}
      />,
    )
    expect(screen.queryByText(/allowlist/i)).toBeNull()
    expect(screen.getByText('这个问法没法安全查到数据，选一个继续：')).toBeTruthy()
    expect(screen.getByRole('button', {name: '本月 GMV 是多少？'})).toBeTruthy()
  })

  it('hides choice buttons after the interrupt is resolved', () => {
    render(
      <InterruptPanel
        interrupt={{
          kind: 'query_error',
          message: '想看各品类的哪项数据？',
          candidates: [{id: 'gmv', label: '各品类 GMV'}],
          resolved: true,
        }}
        role="operator"
        onResume={() => undefined}
        onPick={() => undefined}
      />,
    )
    expect(screen.queryByText('请选择')).toBeNull()
    expect(screen.queryByRole('button', {name: '各品类 GMV'})).toBeNull()
  })

  it('hides write confirm after the interrupt is resolved', () => {
    render(
      <InterruptPanel
        interrupt={{
          kind: 'write_preview',
          operation_id: 'op-1',
          operation_type: 'update_sku_status',
          resolved: true,
        }}
        role="operator"
        onResume={() => undefined}
      />,
    )
    expect(screen.getByText('写入预览')).toBeTruthy()
    expect(screen.queryByRole('button', {name: '确认'})).toBeNull()
  })

  it('falls back to suggested asks when query error has no candidates', () => {
    const onPick = vi.fn()
    render(
      <InterruptPanel
        interrupt={{
          kind: 'query_error',
          error_code: 'SCHEMA_GAP',
          error_message: 'dim_category.category_name',
          schema_gap: {missing_concept: 'dim_category.category_name'},
        }}
        role="operator"
        onResume={() => undefined}
        onPick={onPick}
        exclude="各品类销售对比"
      />,
    )
    expect(screen.getByText('请选择')).toBeTruthy()
    expect(screen.getByText('这个问法还缺条件，选一个继续：')).toBeTruthy()
    expect(screen.queryByText('SCHEMA_GAP')).toBeNull()
    expect(screen.queryByText('dim_category.category_name')).toBeNull()
    expect(screen.queryByRole('button', {name: '各品类销售对比'})).toBeNull()
    screen.getByRole('button', {name: '各品类 GMV'}).click()
    expect(onPick).toHaveBeenCalledWith('各品类 GMV')
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
    expect(screen.getByText('请选择')).toBeTruthy()
    expect(screen.getByText('缺少仓储表')).toBeTruthy()
    expect(screen.getByRole('button', {name: '本月 GMV 是多少？'})).toBeTruthy()
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
