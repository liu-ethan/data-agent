import {useEffect, useRef} from 'react'
import type {ChartDsl, ResultPage} from '../types'

const ALLOWED = new Set(['bar', 'line', 'horizontal_bar'])

export function ChartRenderer({dsl, result}: {dsl: ChartDsl; result: ResultPage}) {
  const ref = useRef<HTMLDivElement>(null)
  const allowed = ALLOWED.has(dsl.type)

  useEffect(() => {
    if (!allowed) return
    let disposed = false
    let cleanup = () => {}
    void import('echarts').then(echarts => {
      if (disposed || !ref.current) return
      const chart = echarts.init(ref.current)
      const rows = result.rows ?? []
      const categories = rows.map(row => String(row[dsl.category_field] ?? ''))
      const values = rows.map(row => Number(row[dsl.value_field] ?? 0))
      chart.setOption({
        animationDuration: 240,
        grid: {left: 52, right: 24, top: 30, bottom: 46},
        tooltip: {trigger: 'axis'},
        xAxis: dsl.type === 'horizontal_bar' ? {type: 'value'} : {type: 'category', data: categories},
        yAxis: dsl.type === 'horizontal_bar' ? {type: 'category', data: categories} : {type: 'value'},
        series: [{
          type: dsl.type === 'line' ? 'line' : 'bar',
          data: values,
          itemStyle: {color: '#0E7C7B'},
        }],
        textStyle: {fontFamily: 'IBM Plex Sans, sans-serif'},
      })
      const resize = () => chart.resize()
      addEventListener('resize', resize)
      cleanup = () => {
        removeEventListener('resize', resize)
        chart.dispose()
      }
    })
    return () => {
      disposed = true
      cleanup()
    }
  }, [allowed, dsl, result])

  if (!allowed) {
    return <p className="chart-rejected" role="status">图表类型不在白名单中，已拒绝渲染。</p>
  }
  return (
    <section className="chart-panel" aria-label="结果图表">
      <header className="chart-panel-head">
        <b>受控图表</b>
        <small>{dsl.category_field} × {dsl.value_field}</small>
      </header>
      <div ref={ref} className="chart-canvas" />
    </section>
  )
}
