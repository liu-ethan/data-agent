import {useMemo, useState} from 'react'
import type {ResultPage} from '../types'

export const EMPTY_RESULT_COPY = '没有符合条件的数据'

export function ResultTable({
  result,
  onDownload,
  onPage,
}: {
  result: ResultPage
  onDownload: () => void
  onPage?: (offset: number) => void
}) {
  const columns = useMemo(() => {
    const first = (result.rows ?? [])[0]
    return first ? Object.keys(first) : []
  }, [result.rows])
  const [sortKey, setSortKey] = useState<string>()
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc')
  const rows = useMemo(() => {
    const source = result.rows ?? []
    if (!sortKey) return source
    const copy = [...source]
    copy.sort((left, right) => {
      const a = String(left[sortKey] ?? '')
      const b = String(right[sortKey] ?? '')
      return sortDir === 'asc' ? a.localeCompare(b, 'zh') : b.localeCompare(a, 'zh')
    })
    return copy
  }, [result.rows, sortDir, sortKey])
  const empty = result.total === 0 || rows.length === 0
  const page = Math.floor(result.offset / result.limit) + 1
  const pages = Math.max(1, Math.ceil(result.total / result.limit))

  function toggleSort(column: string) {
    if (sortKey === column) setSortDir(current => current === 'asc' ? 'desc' : 'asc')
    else {
      setSortKey(column)
      setSortDir('asc')
    }
  }

  return (
    <section className="result-panel" aria-label="结果表">
      <header className="result-panel-head">
        <div>
          <span className="result-panel-eyebrow">结果表</span>
          {empty
            ? null
            : <span className="result-panel-meta">{result.total} 行 · 第 {page} / {pages} 页</span>}
        </div>
        <div className="result-actions">
          {onPage && !empty && (
            <>
              <button
                type="button"
                disabled={result.offset <= 0}
                onClick={() => onPage(Math.max(0, result.offset - result.limit))}
              >
                上一页
              </button>
              <button
                type="button"
                disabled={result.offset + result.limit >= result.total}
                onClick={() => onPage(result.offset + result.limit)}
              >
                下一页
              </button>
            </>
          )}
          <button type="button" className="result-panel-download" onClick={onDownload}>下载 CSV</button>
        </div>
      </header>
      {empty
        ? (
          <div className="result-panel-empty" role="status">
            <b>{EMPTY_RESULT_COPY}</b>
            <span>空结果不是数值 0，可以缩小或调整筛选条件后重试。</span>
          </div>
        )
        : (
          <div className="result-table-wrap" tabIndex={0} aria-label="结果数据表">
            <table>
              <thead>
                <tr>
                  {columns.map(column => (
                    <th key={column} scope="col">
                      <button type="button" onClick={() => toggleSort(column)}>
                        {column}
                      </button>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row, index) => (
                  <tr key={index}>
                    {columns.map(column => <td key={column}>{String(row[column] ?? '—')}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
    </section>
  )
}
