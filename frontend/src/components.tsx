import {useEffect,useRef} from 'react'
import type {ChartDsl, ResultPage} from './types'

// Lightweight chart renderer kept for backward compatibility with legacy tests
// and the optional chart artifact. The dashboard surfaces ResultTable by
// default and renders this when the runtime emits a CHART_DSL artifact.
export function Chart({dsl,result}:{dsl:ChartDsl;result:ResultPage}){
  const ref=useRef<HTMLDivElement>(null)
  useEffect(()=>{let disposed=false,cleanup=()=>{};void import('echarts').then(echarts=>{if(disposed||!ref.current)return;const chart=echarts.init(ref.current),rows=result.rows??[],categories=rows.map(row=>String(row[dsl.category_field]??'')),values=rows.map(row=>Number(row[dsl.value_field]??0));chart.setOption({animationDuration:350,grid:{left:52,right:24,top:30,bottom:46},tooltip:{trigger:'axis'},xAxis:dsl.type==='horizontal_bar'?{type:'value'}:{type:'category',data:categories},yAxis:dsl.type==='horizontal_bar'?{type:'category',data:categories}:{type:'value'},series:[{type:dsl.type==='line'?'line':'bar',data:values,itemStyle:{color:'#5B5FCF'}}],textStyle:{fontFamily:'Inter, sans-serif'}});const resize=()=>chart.resize();addEventListener('resize',resize);cleanup=()=>{removeEventListener('resize',resize);chart.dispose()}});return()=>{disposed=true;cleanup()}},[dsl,result])
  return <section className="chart-panel" aria-label="结果图表"><header className="chart-panel-head"><b>受控图表</b><small>{dsl.category_field} × {dsl.value_field}</small></header><div ref={ref} className="chart-canvas"/></section>
}