import type {Action, StreamEvent} from '../types'

const STAGES:Action[]=['RETRIEVE','GENERATE','EXECUTE','RESPOND']

export function ThinkingPanel({events,defaultOpen=false}:{events:StreamEvent[];defaultOpen?:boolean}){
  const stageRows=STAGES.map((stage,index)=>{
    const completed=[...events].reverse().find(event=>event.action===stage&&event.event==='node.completed')
    const started=[...events].reverse().find(event=>event.action===stage&&event.event==='node.started')
    const status=completed?'done':started?'running':'idle'
    const duration=completed?.duration_ms??null
    return {stage,index,status,duration}
  })
  const completedCount=stageRows.filter(row=>row.status==='done').length
  const totalMs=stageRows.reduce((sum,row)=>sum+(row.duration??0),0)
  const summary=events.length===0
    ?'等待开始'
    :`${completedCount} / ${STAGES.length} 阶段${totalMs>0?` · ${Math.round(totalMs)} ms`:''}`
  return <details className="thinking-panel" open={defaultOpen}>
    <summary><span className="thinking-chevron" aria-hidden>▸</span>思考过程<span className="thinking-summary">{summary}</span></summary>
    <ol className="thinking-rail">
      {stageRows.map(row=><li key={row.stage} className={`thinking-row ${row.status}`}>
        <span className="thinking-dot" aria-hidden>{row.status==='done'?'✓':row.index+1}</span>
        <span className="thinking-stage">{row.stage}</span>
        <span className="thinking-status">{row.status==='done'?'完成':row.status==='running'?'运行中':'等待中'}</span>
        <span className="thinking-duration">{row.duration!==null?`${Math.round(row.duration)} ms`:'—'}</span>
      </li>)}
    </ol>
  </details>
}