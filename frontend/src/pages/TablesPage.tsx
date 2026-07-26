import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  getTablePage,
  listTables,
  type TablePage,
  type TableSummary,
} from '../api/tables'
import { appConfig } from '../config'

const PAGE_SIZE = appConfig.tablesPageSize

export default function TablesPage() {
  const navigate = useNavigate()
  const [tables, setTables] = useState<TableSummary[]>([])
  const [selectedTable, setSelectedTable] = useState<string | null>(null)
  const [page, setPage] = useState(1)
  const [tablePage, setTablePage] = useState<TablePage | null>(null)
  const [tablesLoading, setTablesLoading] = useState(true)
  const [detailLoading, setDetailLoading] = useState(false)
  const [tablesError, setTablesError] = useState<string | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)
  const [reloadKey, setReloadKey] = useState(0)

  useEffect(() => {
    let cancelled = false
    setTablesLoading(true)
    setTablesError(null)

    listTables()
      .then((items) => {
        if (!cancelled) setTables(items)
      })
      .catch((error) => {
        if (!cancelled) {
          setTablesError(
            error instanceof Error ? error.message : '加载数据表失败',
          )
        }
      })
      .finally(() => {
        if (!cancelled) setTablesLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [reloadKey])

  useEffect(() => {
    if (!selectedTable) {
      setTablePage(null)
      return
    }

    let cancelled = false
    setDetailLoading(true)
    setDetailError(null)

    getTablePage(selectedTable, page)
      .then((data) => {
        if (!cancelled) setTablePage(data)
      })
      .catch((error) => {
        if (!cancelled) {
          setDetailError(
            error instanceof Error ? error.message : '加载表数据失败',
          )
        }
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [selectedTable, page, reloadKey])

  function selectTable(name: string) {
    setSelectedTable(name)
    setPage(1)
    setTablePage(null)
    setDetailError(null)
  }

  const totalPages = tablePage
    ? Math.max(1, Math.ceil(tablePage.total_rows / PAGE_SIZE))
    : 1

  return (
    <div className="min-h-screen bg-bg text-ink">
      <header className="border-b border-line bg-surface">
        <div className="mx-auto flex max-w-[1440px] items-center justify-between gap-4 px-5 py-4 sm:px-8">
          <div>
            <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-accent">
              Data catalog
            </p>
            <h1 className="font-display text-xl tracking-tight">业务数据表</h1>
          </div>
          <button
            type="button"
            onClick={() => navigate('/app')}
            className="rounded-lg border border-line bg-surface px-4 py-2 text-sm font-medium text-ink transition-colors hover:border-accent hover:text-accent focus:outline-none focus:ring-2 focus:ring-accent/30"
          >
            ← 返回工作台
          </button>
        </div>
      </header>

      <main className="mx-auto max-w-[1440px] px-5 py-6 sm:px-8 sm:py-8">
        <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="font-display text-2xl tracking-tight">表概览</h2>
            <p className="mt-1 text-sm text-muted">
              浏览可访问的业务表、字段结构与原始数据。
            </p>
          </div>
          {!tablesLoading && !tablesError && (
            <p className="font-mono text-xs text-muted">
              {tables.length} 张业务表 · 只读
            </p>
          )}
        </div>

        {tablesLoading ? (
          <StatusPanel text="正在加载数据表…" />
        ) : tablesError ? (
          <ErrorPanel
            message={tablesError}
            onRetry={() => setReloadKey((value) => value + 1)}
          />
        ) : tables.length === 0 ? (
          <StatusPanel text="当前没有可访问的业务表。" />
        ) : (
          <div className="grid items-start gap-6 lg:grid-cols-[280px_minmax(0,1fr)]">
            <aside className="rounded-xl border border-line bg-surface p-3">
              <ul className="space-y-1">
                {tables.map((table) => {
                  const active = table.name === selectedTable
                  return (
                    <li key={table.name}>
                      <button
                        type="button"
                        onClick={() => selectTable(table.name)}
                        aria-pressed={active}
                        className={`w-full rounded-lg px-3 py-3 text-left transition-colors focus:outline-none focus:ring-2 focus:ring-accent/30 ${
                          active
                            ? 'bg-accent-soft text-accent'
                            : 'hover:bg-bg'
                        }`}
                      >
                        <span className="block truncate font-mono text-sm font-semibold">
                          {table.name}
                        </span>
                        <span className="mt-1 block text-xs text-muted">
                          {formatCount(table.column_count)} 列 ·{' '}
                          {formatCount(table.row_count)} 行
                        </span>
                      </button>
                    </li>
                  )
                })}
              </ul>
            </aside>

            <section className="min-w-0">
              {!selectedTable ? (
                <div className="rounded-xl border border-dashed border-line bg-surface px-6 py-20 text-center">
                  <p className="font-display text-lg">选择一张表查看数据</p>
                  <p className="mt-2 text-sm text-muted">
                    字段结构可折叠，数据固定每页显示 50 条。
                  </p>
                </div>
              ) : detailLoading && !tablePage ? (
                <StatusPanel text={`正在读取 ${selectedTable}…`} />
              ) : detailError ? (
                <ErrorPanel
                  message={detailError}
                  onRetry={() => setReloadKey((value) => value + 1)}
                />
              ) : tablePage ? (
                <TableDetail
                  data={tablePage}
                  loading={detailLoading}
                  totalPages={totalPages}
                  onPageChange={setPage}
                />
              ) : null}
            </section>
          </div>
        )}
      </main>
    </div>
  )
}

function TableDetail({
  data,
  loading,
  totalPages,
  onPageChange,
}: {
  data: TablePage
  loading: boolean
  totalPages: number
  onPageChange: (page: number) => void
}) {
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="font-mono text-xl font-semibold text-accent">{data.name}</p>
          <p className="mt-1 text-xs text-muted">
            {formatCount(data.columns.length)} 个可见字段 ·{' '}
            {formatCount(data.total_rows)} 行
          </p>
        </div>
        <p className="rounded-md bg-accent-soft px-2.5 py-1 font-mono text-[11px] text-accent">
          READ ONLY
        </p>
      </div>

      <details className="group rounded-xl border border-line bg-surface">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 text-sm font-medium focus:outline-none focus:ring-2 focus:ring-inset focus:ring-accent/30">
          <span>字段说明</span>
          <span className="text-xs text-muted group-open:hidden">
            {data.columns.length} 个字段 · 展开
          </span>
          <span className="hidden text-xs text-muted group-open:inline">收起</span>
        </summary>
        <div className="grid gap-px border-t border-line bg-line sm:grid-cols-2 xl:grid-cols-3">
          {data.columns.map((column) => (
            <div key={column.name} className="bg-surface px-4 py-3">
              <p className="truncate font-mono text-sm font-medium">{column.name}</p>
              <p className="mt-1 text-xs text-muted">
                {column.type} · {column.nullable ? '可为空' : '不可为空'}
              </p>
            </div>
          ))}
        </div>
      </details>

      <div className="overflow-hidden rounded-xl border border-line bg-surface">
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-left text-sm">
            <thead className="bg-bg">
              <tr>
                {data.columns.map((column) => (
                  <th
                    key={column.name}
                    scope="col"
                    className="whitespace-nowrap border-b border-line px-4 py-3 font-mono text-xs font-semibold text-muted"
                  >
                    {column.name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className={loading ? 'opacity-45' : undefined}>
              {data.rows.map((row, rowIndex) => (
                <tr
                  key={rowIndex}
                  className="border-b border-line last:border-b-0 hover:bg-bg/70"
                >
                  {data.columns.map((column) => (
                    <td
                      key={column.name}
                      className="max-w-80 whitespace-nowrap px-4 py-3 font-mono text-xs"
                      title={formatCell(row[column.name])}
                    >
                      <span className="block max-w-80 truncate">
                        {formatCell(row[column.name])}
                      </span>
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {data.rows.length === 0 && (
          <p className="px-4 py-12 text-center text-sm text-muted">此页没有数据。</p>
        )}
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line px-4 py-3">
          <p className="font-mono text-xs text-muted">
            第 {data.page} / {totalPages} 页 · 每页 {data.page_size} 条
          </p>
          <div className="flex gap-2">
            <PageButton
              disabled={loading || data.page <= 1}
              onClick={() => onPageChange(data.page - 1)}
            >
              上一页
            </PageButton>
            <PageButton
              disabled={loading || data.page >= totalPages}
              onClick={() => onPageChange(data.page + 1)}
            >
              下一页
            </PageButton>
          </div>
        </div>
      </div>
    </div>
  )
}

function PageButton({
  children,
  disabled,
  onClick,
}: {
  children: string
  disabled: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className="rounded-md border border-line px-3 py-1.5 text-xs font-medium transition-colors hover:border-accent hover:text-accent focus:outline-none focus:ring-2 focus:ring-accent/30 disabled:cursor-not-allowed disabled:opacity-40"
    >
      {children}
    </button>
  )
}

function StatusPanel({ text }: { text: string }) {
  return (
    <div className="rounded-xl border border-line bg-surface px-6 py-20 text-center text-sm text-muted">
      {text}
    </div>
  )
}

function ErrorPanel({
  message,
  onRetry,
}: {
  message: string
  onRetry: () => void
}) {
  return (
    <div
      role="alert"
      className="rounded-xl border border-red-200 bg-red-50 px-6 py-10 text-center"
    >
      <p className="text-sm text-red-700">{message}</p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-4 rounded-md border border-red-200 bg-white px-3 py-1.5 text-xs font-medium text-red-700 transition-colors hover:border-red-400"
      >
        重新加载
      </button>
    </div>
  )
}

function formatCount(value: number): string {
  return new Intl.NumberFormat('zh-CN').format(value)
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}
