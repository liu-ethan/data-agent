import { appConfig } from '../config'
import { apiFetch } from './client'

export interface TableSummary {
  name: string
  column_count: number
  row_count: number
}

export interface TablePage {
  name: string
  columns: { name: string; type: string; nullable: boolean }[]
  page: number
  page_size: number
  total_rows: number
  rows: Record<string, unknown>[]
}

export async function listTables(): Promise<TableSummary[]> {
  const res = await apiFetch('/api/tables')
  if (!res.ok) throw new Error('加载数据表失败')
  return (await res.json()).tables ?? []
}

export async function getTablePage(name: string, page: number): Promise<TablePage> {
  const res = await apiFetch(
    `/api/tables/${encodeURIComponent(name)}?page=${page}&page_size=${appConfig.tablesPageSize}`,
  )
  if (!res.ok) throw new Error('加载表数据失败')
  return res.json()
}
