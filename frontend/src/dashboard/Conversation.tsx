import {useEffect,useState,type KeyboardEvent} from 'react'
import type {ChartDsl, Identity, Interrupt, Message, ResultPage, StreamEvent, ThreadDetail, UserPreferences} from '../types'
import {EmptyState} from './EmptyState'
import {MessageBubble} from './MessageBubble'

export function Conversation({
  messages,events,interrupt,chartDsl,result,busy,connection,error,recommended,
  identity,preferences,threadTitle,onSend,onPick,onResume,onStop,onDownload,
}:{
  messages:Message[]
  events:StreamEvent[]
  interrupt?:Interrupt
  chartDsl?:ChartDsl
  result?:ResultPage
  busy:boolean
  connection:'ready'|'running'|'reconnecting'|'offline'
  error?:string
  recommended:string[]
  identity?:Identity
  preferences:UserPreferences
  threadTitle?:string
  onSend:(question:string)=>void
  onPick:(question:string)=>void
  onResume:(answer:string)=>void
  onStop:()=>void
  onDownload:()=>void
}){
  const[question,setQuestion]=useState(''),[clarification,setClarification]=useState('')
  const placeholder=messages.length===0?'例如：昨天各品类的 GMV 是多少？':'继续提问，例如：拆到一级品类'
  useEffect(()=>{if(!busy){setClarification('')}},[busy])
  function handleKey(event:KeyboardEvent<HTMLTextAreaElement>){if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();const text=question.trim();if(text){onSend(text);setQuestion('')}}}
  return <section className="conversation" aria-label="对话区">
    <header className="conversation-head">
      <div>
        <p className="conversation-eyebrow">{messages.length?'当前会话':'新会话'}</p>
        <h2 className="conversation-title">{threadTitle??(messages.length?'分析记录':'今天要查什么？')}</h2>
      </div>
      <div className={`connection connection-${connection}`} role="status">
        <i aria-hidden/>{connection==='ready'?'已连接':connection==='running'?'正在执行':connection==='reconnecting'?'正在重连':'连接中断'}
      </div>
    </header>
    <div className="conversation-scroll">
      {messages.length===0
        ? <EmptyState identity={identity} recommended={recommended} onPick={onPick}/>
        : messages.map((message,index)=><MessageBubble key={`${message.role}-${index}-${message.content.slice(0,24)}`} message={message} events={events} chartDsl={chartDsl} result={result} interrupt={index===messages.length-1?interrupt:undefined}/>)}
      {busy&&<div className="bubble bubble-assistant bubble-running" aria-live="polite">
        <header className="bubble-head"><span className="bubble-label">Runtime</span></header>
        <div className="bubble-body"><span className="pulse" aria-hidden/>{connection==='reconnecting'?'正在恢复实时连接…':'正在运行分析…'}</div>
      </div>}
      {result&&<ResultPanel result={result} onDownload={onDownload}/>}
      {error&&<div className="error-banner" role="alert">{error}</div>}
    </div>
    {interrupt&&<form className="clarify" onSubmit={event=>{event.preventDefault();const answer=clarification.trim();if(answer){onResume(answer);setClarification('')}}}>
      <label htmlFor="clarify-input">补充信息以继续：<b>{interrupt.question}</b></label>
      <div className="clarify-input-row">
        <input id="clarify-input" name="clarification" value={clarification} onChange={event=>setClarification(event.target.value)} placeholder="用一句话回答"/>
        <button type="submit" className="clarify-send" disabled={busy||!clarification.trim()}>继续</button>
      </div>
      {(interrupt.candidates??[]).length>0&&<div className="clarify-candidates">
        {(interrupt.candidates??[]).map(candidate=><button key={candidate} type="button" className="clarify-chip" onClick={()=>onResume(candidate)} disabled={busy}>{candidate}</button>)}
      </div>}
    </form>}
    <div className="composer">
      <textarea aria-label="问题" value={question} onChange={event=>setQuestion(event.target.value)} onKeyDown={handleKey} placeholder={placeholder}/>
      {busy
        ? <button type="button" className="composer-stop" aria-label="停止" onClick={onStop}>停止</button>
        : <button type="button" className="composer-send" aria-label="发送" onClick={()=>{const text=question.trim();if(text){onSend(text);setQuestion('')}}} disabled={!question.trim()}>发送 ↗</button>}
      <span className="composer-hint">Enter 发送 · Shift + Enter 换行 · 默认时区 {String(preferences.values?.timezone??'Asia/Shanghai')}</span>
    </div>
  </section>
}

function ResultPanel({result,onDownload}:{result:ResultPage;onDownload:()=>void}){
  const rows=result.rows??[],columns=rows[0]?Object.keys(rows[0]):[]
  return <section className="result-panel" aria-label="结果表">
    <header className="result-panel-head">
      <div>
        <span className="result-panel-eyebrow">结果表</span>
        <span className="result-panel-meta">{result.total} 行 · 第 {result.offset/result.limit+1} / {Math.max(1,Math.ceil(result.total/result.limit))} 页</span>
      </div>
      <button type="button" className="result-panel-download" onClick={onDownload}>下载 CSV</button>
    </header>
    {rows.length===0
      ? <div className="result-panel-empty" role="status"><b>没有符合条件的数据</b><span>空结果不等于数值 0，可以尝试调整时间范围。</span></div>
      : <div className="result-table-wrap" tabIndex={0} aria-label="结果数据表">
          <table>
            <thead><tr>{columns.map(column=><th key={column} scope="col">{column}</th>)}</tr></thead>
            <tbody>{rows.map((row,index)=><tr key={index}>{columns.map(column=><td key={column}>{String(row[column]??'—')}</td>)}</tr>)}</tbody>
          </table>
        </div>}
  </section>
}