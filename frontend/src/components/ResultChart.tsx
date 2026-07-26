import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

export type ChartConfig = {
  type: string
  x: string
  y: string
  title?: string
  series?: string[]
}

const PIE_COLORS = ['#0F766E', '#B45309', '#1D4ED8', '#BE123C', '#4338CA', '#047857']
const LINE_COLORS = ['#0F766E', '#B45309', '#1D4ED8', '#BE123C']

type Props = {
  chart: ChartConfig | null
  rows: Record<string, unknown>[]
}

export default function ResultChart({ chart, rows }: Props) {
  if (!chart || chart.type === 'table' || !chart.x || !chart.y || rows.length === 0) {
    return null
  }

  const seriesKeys =
    Array.isArray(chart.series) && chart.series.length > 0
      ? chart.series.filter(Boolean)
      : [chart.y]

  const data = rows.map((row) => {
    const next: Record<string, unknown> = { ...row }
    for (const key of seriesKeys) {
      next[key] = toNumber(row[key])
    }
    return next
  })

  const useDualAxis =
    chart.type === 'line' &&
    seriesKeys.length > 1 &&
    scalesDiffer(data, seriesKeys[0], seriesKeys[1])

  return (
    <section className="rounded-xl border border-line bg-surface p-4">
      <h2 className="text-xs font-medium uppercase tracking-wider text-muted">
        图表
        {chart.title ? ` · ${chart.title}` : ''}
      </h2>
      <div className="mt-3 h-72 w-full">
        <ResponsiveContainer width="100%" height="100%">
          {chart.type === 'line' ? (
            <LineChart data={data}>
              <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" />
              <XAxis dataKey={chart.x} tick={{ fontSize: 11 }} />
              <YAxis
                yAxisId="left"
                tick={{ fontSize: 11 }}
                width={48}
              />
              {useDualAxis && (
                <YAxis
                  yAxisId="right"
                  orientation="right"
                  tick={{ fontSize: 11 }}
                  width={56}
                />
              )}
              <Tooltip />
              <Legend />
              {seriesKeys.map((key, index) => (
                <Line
                  key={key}
                  type="monotone"
                  yAxisId={
                    useDualAxis && index > 0 ? 'right' : 'left'
                  }
                  dataKey={key}
                  name={key}
                  stroke={LINE_COLORS[index % LINE_COLORS.length]}
                  strokeWidth={2}
                  dot={false}
                />
              ))}
            </LineChart>
          ) : chart.type === 'pie' ? (
            <PieChart>
              <Tooltip />
              <Legend />
              <Pie
                data={data}
                dataKey={chart.y}
                nameKey={chart.x}
                outerRadius={100}
                label
              >
                {data.map((_, i) => (
                  <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />
                ))}
              </Pie>
            </PieChart>
          ) : (
            <BarChart data={data}>
              <CartesianGrid strokeDasharray="3 3" stroke="#E5E7EB" />
              <XAxis dataKey={chart.x} tick={{ fontSize: 11 }} />
              <YAxis tick={{ fontSize: 11 }} />
              <Tooltip />
              <Legend />
              {seriesKeys.map((key, index) => (
                <Bar
                  key={key}
                  dataKey={key}
                  name={key}
                  fill={LINE_COLORS[index % LINE_COLORS.length]}
                  radius={[4, 4, 0, 0]}
                />
              ))}
            </BarChart>
          )}
        </ResponsiveContainer>
      </div>
    </section>
  )
}

function scalesDiffer(
  data: Record<string, unknown>[],
  a: string,
  b: string,
): boolean {
  const maxA = Math.max(...data.map((row) => Math.abs(toNumber(row[a]))), 0)
  const maxB = Math.max(...data.map((row) => Math.abs(toNumber(row[b]))), 0)
  if (maxA === 0 || maxB === 0) return false
  const ratio = Math.max(maxA, maxB) / Math.min(maxA, maxB)
  return ratio >= 10
}

function toNumber(value: unknown): number {
  if (typeof value === 'number') return value
  if (typeof value === 'string') {
    const n = Number(value)
    return Number.isFinite(n) ? n : 0
  }
  return 0
}
