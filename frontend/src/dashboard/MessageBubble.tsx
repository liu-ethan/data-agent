import type {Message, StreamEvent, ChartDsl, ResultPage, Interrupt} from '../types'
import {ThinkingPanel} from './ThinkingPanel'

export function MessageBubble({message,events,chartDsl,result,interrupt}:{
  message:Message
  events:StreamEvent[]
  chartDsl?:ChartDsl
  result?:ResultPage
  interrupt?:Interrupt
}){
  const isUser=message.role==='user'
  return <article className={`bubble ${isUser?'bubble-user':'bubble-assistant'}`}>
    <header className="bubble-head">
      <span className="bubble-label">{isUser?'你':'Runtime'}</span>
      {message.created_at&&<time className="bubble-time">{new Date(message.created_at).toLocaleTimeString()}</time>}
    </header>
    {!isUser&&events.length>0&&<ThinkingPanel events={events}/>}
    <div className="bubble-body">{message.content||'运行结束'}</div>
    {interrupt&&<aside className="bubble-interrupt" role="status">
      <b>需要补充信息</b>
      <p>{interrupt.question}</p>
      <div className="interrupt-candidates">{(interrupt.candidates??[]).map(candidate=><span key={candidate}>{candidate}</span>)}</div>
    </aside>}
  </article>
}