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
}

const PIE_COLORS = ['#0F766E', '#B45309', '#1D4ED8', '#BE123C', '#4338CA', '#047857']

type Props = {
  chart: ChartConfig | null
  rows: Record<string, unknown>[]
}

export default function ResultChart({ chart, rows }: Props) {
  if (!chart || chart.type === 'table' || !chart.x || !chart.y || rows.length === 0) {
    return null
  }

  const data = rows.map((row) => ({
    ...row,
    [chart.y]: toNumber(row[chart.y]),
  }))

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
              <YAxis tick={{ fontSize: 11 }} />
              <Tooltip />
              <Legend />
              <Line
                type="monotone"
                dataKey={chart.y}
                stroke="#0F766E"
                strokeWidth={2}
                dot={false}
              />
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
              <Bar dataKey={chart.y} fill="#0F766E" radius={[4, 4, 0, 0]} />
            </BarChart>
          )}
        </ResponsiveContainer>
      </div>
    </section>
  )
}

function toNumber(value: unknown): number {
  if (typeof value === 'number') return value
  if (typeof value === 'string') {
    const n = Number(value)
    return Number.isFinite(n) ? n : 0
  }
  return 0
}
