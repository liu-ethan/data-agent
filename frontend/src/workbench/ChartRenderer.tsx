import type {ResultPage} from '../types'

export function ChartRenderer({result}: {result: ResultPage}) {
  const numeric = result.columns.find(column =>
    result.rows.some(row => typeof row[column] === 'number'),
  )
  const label = result.columns.find(column => column !== numeric)
  if (!numeric || result.rows.length < 2) return null
  const values = result.rows.map(row => Number(row[numeric]) || 0)
  const max = Math.max(...values, 1)
  const height = 120
  const gap = 8
  const barWidth = Math.max(12, Math.min(36, 280 / values.length - gap))
  const width = values.length * (barWidth + gap) + 8

  return (
    <svg className="chart" viewBox={`0 0 ${width} ${height + 24}`} role="img" aria-label="结果简图">
      {values.map((value, index) => {
        const h = (value / max) * height
        const x = 4 + index * (barWidth + gap)
        return (
          <g key={index}>
            <rect x={x} y={height - h} width={barWidth} height={h} rx="4" fill="#3B6CFF" />
            {label ? (
              <text x={x + barWidth / 2} y={height + 14} textAnchor="middle" fontSize="9" fill="#8E8E93">
                {String(result.rows[index][label] ?? '').slice(0, 6)}
              </text>
            ) : null}
          </g>
        )
      })}
    </svg>
  )
}
