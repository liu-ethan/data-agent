import {useState} from 'react'
import {requestJson} from '../client'
import {readToken} from '../auth/session'
import type {ResultPage} from '../types'

export function ResultTable({result}: {result: ResultPage}) {
  const [page, setPage] = useState(result)
  const [busy, setBusy] = useState(false)
  const limit = page.limit || 20
  const offset = page.offset || 0
  const canPrev = offset > 0
  const canNext = offset + page.rows.length < page.row_count

  async function load(nextOffset: number) {
    const token = readToken()
    if (!token) return
    setBusy(true)
    try {
      const data = await requestJson<ResultPage>(
        `/api/results/${page.result_id}?offset=${nextOffset}&limit=${limit}`,
        token,
      )
      setPage(data)
    } finally {
      setBusy(false)
    }
  }

  async function downloadCsv() {
    const token = readToken()
    if (!token) return
    const res = await fetch(`/api/results/${page.result_id}.csv`, {
      headers: {Authorization: `Bearer ${token}`},
    })
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `${page.result_id}.csv`
    link.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="result-block">
      <div className="result-meta">
        {page.time_range?.label ? <span>时间窗 {page.time_range.label}</span> : null}
        {page.data_as_of ? <span>data_as_of {page.data_as_of}</span> : null}
        {page.metric_versions
          ? Object.entries(page.metric_versions).map(([id, version]) => (
              <span key={id}>
                {id} v{version}
              </span>
            ))
          : null}
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              {page.columns.map(column => (
                <th key={column}>{column}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {page.rows.map((row, index) => (
              <tr key={index}>
                {page.columns.map(column => (
                  <td key={column}>{String(row[column] ?? '')}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="table-actions">
        <button type="button" disabled={busy || !canPrev} onClick={() => load(Math.max(0, offset - limit))}>
          上一页
        </button>
        <span>
          {offset + 1}–{offset + page.rows.length} / {page.row_count}
        </span>
        <button type="button" disabled={busy || !canNext} onClick={() => load(offset + limit)}>
          下一页
        </button>
        <button type="button" onClick={() => void downloadCsv()}>
          下载 CSV
        </button>
      </div>
    </div>
  )
}
