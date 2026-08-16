import {StrictMode,useEffect,useRef,useState} from 'react'
import {createRoot} from 'react-dom/client'
import type {components} from './api/schema'
import {API,ApiError,consumeSse,isChartDsl,newClientRequestId,requestJson,requestRecommendedQuestions,tokenExpiry} from './client'
import {AuthPage} from './auth/AuthPage'
import {Chart} from './components'
import {Conversation} from './dashboard/Conversation'
import {Sidebar} from './dashboard/Sidebar'
import type {ArtifactRecord,ChartDsl,ChatResult,Identity,Interrupt,Message,ResultPage,StreamEvent,ThreadDetail,ThreadSummary,UserPreferences} from './types'
import './styles.css'

const PAGE_SIZE=50
const DEFAULT_RECOMMENDED=[
  '昨天各品类的 GMV 是多少？',
  '昨天销售额是多少？',
  '昨天有多少已支付订单？',
  '昨天每个店铺的支付买家数？',
  '上周退款总金额是多少？',
  'orders 表有哪些字段？',
  '昨天哪几个品类的退款最多？',
  '最近 7 天日均 GMV？',
  '各品类订单占比？',
  'products 表有哪些字段？',
]

function Workbench({token,logout}:{token:string;logout:()=>void}){
  const[identity,setIdentity]=useState<Identity>()
  const[threads,setThreads]=useState<ThreadSummary[]>([])
  const[messages,setMessages]=useState<Message[]>([])
  const[question,setQuestion]=useState('')
  const[events,setEvents]=useState<StreamEvent[]>([])
  const[result,setResult]=useState<ResultPage>()
  const[resultId,setResultId]=useState<string>()
  const[chartDsl,setChartDsl]=useState<ChartDsl>()
  const[busy,setBusy]=useState(false)
  const[threadId,setThreadId]=useState<string>()
  const[requestId,setRequestId]=useState<string>()
  const[stateVersion,setStateVersion]=useState<number>()
  const[interrupt,setInterrupt]=useState<Interrupt>()
  const[error,setError]=useState<string>()
  const[connection,setConnection]=useState<'ready'|'running'|'reconnecting'|'offline'>('ready')
  const[preferences,setPreferences]=useState<UserPreferences>({values:{},schema_version:'user_preferences_v1'})
  const[recommended,setRecommended]=useState<string[]>(DEFAULT_RECOMMENDED)
  const[threadTitles,setThreadTitles]=useState<Record<string,string>>({})
  const[route,setRoute]=useState<string>(()=>location.pathname)
  const streamAbort=useRef<AbortController|null>(null)
  const resumeKeys=useRef(new Map<string,string>())
  const showSettings=route==='/app/settings'

  function navigate(path:string,replace=false){
    if(replace)history.replaceState({},'',path)
    else history.pushState({},'',path)
    setRoute(path)
  }

  async function refreshThreads(){
    const value=await requestJson<components['schemas']['ThreadListResponse']>('/api/threads',token)
    const items=value.items??[]
    setThreads(items)
    setThreadTitles(prev=>{const next={...prev};for(const item of items)if(!next[item.thread_id])next[item.thread_id]=item.title;return next})
  }

  async function loadResult(id:string){
    const value=await requestJson<ResultPage>(`/api/results/${id}?offset=0&limit=${PAGE_SIZE}`,token)
    setResult(value);setResultId(id)
  }

  async function loadArtifacts(ids:string[]){
    setChartDsl(undefined)
    for(const id of ids){
      try{
        const record=await requestJson<ArtifactRecord>(`/api/artifacts/${id}`,token)
        if(record.spec.type==='CHART_DSL'&&isChartDsl(record.payload))setChartDsl(record.payload)
      }catch(value){setError(value instanceof Error?value.message:'结果制品无法读取')}
    }
  }

  async function openThread(id:string,push=true){
    try{
      setError(undefined)
      const detail=await requestJson<ThreadDetail>(`/api/threads/${id}`,token)
      const resultIds=detail.result_ids??[]
      setThreadId(id)
      setMessages((detail.messages??[]) as Message[])
      setStateVersion(detail.state_version??undefined)
      setInterrupt(detail.interrupt??undefined)
      setEvents([])
      if(resultIds.at(-1))await loadResult(resultIds.at(-1)!)
      else{setResult(undefined);setResultId(undefined)}
      await loadArtifacts(detail.artifact_ids??[])
      if(push)navigate(`/app/threads/${id}`)
    }catch(value){setError(value instanceof Error?value.message:'线程无法读取')}
  }

  useEffect(()=>{
    let active=true
    void (async()=>{
      try{
        const[me,settings,questions]=await Promise.all([
          requestJson<Identity>('/api/me',token),
          requestJson<UserPreferences>('/api/settings',token),
          requestRecommendedQuestions().catch(()=>DEFAULT_RECOMMENDED),
        ])
        if(!active)return
        setIdentity(me)
        setPreferences(settings)
        setRecommended(questions)
        await refreshThreads()
        const thread=location.pathname.match(/^\/app\/threads\/([^/]+)$/)
        if(thread)await openThread(decodeURIComponent(thread[1]),false)
      }catch(value){
        if(active){
          setError(value instanceof Error?value.message:'工作台初始化失败')
          if(value instanceof ApiError&&value.status===401)logout()
        }
      }
    })()
    const pop=()=>{
      setRoute(location.pathname)
      const thread=location.pathname.match(/^\/app\/threads\/([^/]+)$/)
      if(thread)void openThread(decodeURIComponent(thread[1]),false)
      else{setThreadId(undefined);setMessages([]);setEvents([]);setResult(undefined);setResultId(undefined);setChartDsl(undefined);setInterrupt(undefined)}
    }
    addEventListener('popstate',pop)
    return()=>{active=false;removeEventListener('popstate',pop)}
  },[token])

  function rememberEvent(event:StreamEvent){
    setEvents(old=>event.eventId&&old.some(item=>item.eventId===event.eventId)?old:[...old,event])
  }

  async function handleRunEvent(event:StreamEvent){
    rememberEvent(event)
    if(event.event==='thread.title_updated'&&event.thread_id&&event.thread_title){
      setThreadTitles(prev=>({...prev,[event.thread_id!]:event.thread_title!}))
      setThreads(old=>old.map(item=>item.thread_id===event.thread_id?{...item,title:event.thread_title!}:item))
      return
    }
    if(event.event==='run.started'){
      setThreadId(event.thread_id)
      navigate(`/app/threads/${event.thread_id}`,true)
    }
    if(event.event==='interrupt.created'){
      setThreadId(event.thread_id)
      setStateVersion(event.state_version??undefined)
      setInterrupt(event.interrupt??undefined)
      navigate(`/app/threads/${event.thread_id}`,true)
      await refreshThreads()
    }
    if(event.event==='run.completed'){
      const ids=event.result_ids??[]
      setThreadId(event.thread_id)
      setStateVersion(event.state_version??undefined)
      setInterrupt(event.interrupt??undefined)
      setMessages(old=>[...old,{role:'assistant',content:event.answer??'运行结束'}])
      if(ids[0])await loadResult(ids[0])
      await loadArtifacts(event.artifact_ids??[])
      navigate(`/app/threads/${event.thread_id}`,true)
      await refreshThreads()
    }
    if(event.event==='run.failed'){
      setThreadId(event.thread_id)
      setStateVersion(event.state_version??undefined)
      setMessages(old=>event.answer?[...old,{role:'assistant',content:event.answer}]:old)
      setError(new ApiError(event.error_code??'REQUEST_FAILED').message)
      navigate(`/app/threads/${event.thread_id}`,true)
    }
  }

  async function ask(textArg?:string){
    const text=(textArg??question).trim()
    if(!text||busy)return
    const id=newClientRequestId()
    const controller=new AbortController()
    const params=new URLSearchParams({message:text,timezone:String(preferences.values?.timezone??'Asia/Shanghai'),request_id:id})
    if(threadId){
      params.set('thread_id',threadId)
      if(stateVersion!==undefined)params.set('expected_state_version',String(stateVersion))
    }
    const cursor={lastEventId:0}
    const url=`${API}/api/chat/stream?${params}`
    streamAbort.current=controller
    setMessages(old=>[...old,{role:'user',content:text}])
    setQuestion('')
    setRequestId(id)
    setBusy(true)
    setConnection('running')
    setError(undefined)
    setResult(undefined)
    setResultId(undefined)
    setChartDsl(undefined)
    setEvents([])
    setInterrupt(undefined)
    try{
      for(let attempt=0;attempt<3;attempt++){
        try{
          await consumeSse({url,token,requestId:id,cursor,signal:controller.signal,onEvent:handleRunEvent})
          setConnection('ready')
          break
        }catch(value){
          if(controller.signal.aborted)throw value
          if(!(value instanceof TypeError)||attempt===2)throw value
          setConnection('reconnecting')
        }
      }
    }catch(value){
      if(controller.signal.aborted){
        setConnection('ready')
        setError('已停止接收进度。服务端可能仍在执行，可从侧栏重新打开。')
      }else{
        setConnection('offline')
        setError(value instanceof Error?value.message:'实时连接已中断，请重新打开会话。')
      }
    }finally{
      if(streamAbort.current===controller)streamAbort.current=null
      setBusy(false)
    }
  }

  async function resume(answer:string){
    if(!interrupt||!threadId||busy)return
    const key=resumeKeys.current.get(interrupt.interrupt_id)??newClientRequestId()
    resumeKeys.current.set(interrupt.interrupt_id,key)
    setBusy(true)
    setError(undefined)
    try{
      if(stateVersion===undefined)throw new Error('缺少会话版本，请重新打开会话。')
      const payload=await requestJson<ChatResult>(`/api/threads/${threadId}/interrupts/${interrupt.interrupt_id}/resume`,token,{
        method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({answer,client_request_id:key,expected_state_version:stateVersion}),
      })
      const ids=payload.result_ids??[]
      resumeKeys.current.delete(interrupt.interrupt_id)
      setStateVersion(payload.state_version??undefined)
      setInterrupt(payload.interrupt??undefined)
      setMessages(old=>[...old,{role:'user',content:answer},{role:'assistant',content:payload.answer??'运行结束'}])
      if(ids[0])await loadResult(ids[0])
      await loadArtifacts(payload.artifact_ids??[])
      await refreshThreads()
    }catch(value){setError(value instanceof Error?value.message:'恢复失败')}
    finally{setBusy(false)}
  }

  async function downloadCsv(){
    if(!resultId)return
    const response=await fetch(`${API}/api/results/${resultId}/export.csv`,{headers:{Authorization:`Bearer ${token}`}})
    if(!response.ok){setError('CSV 导出失败，请确认结果仍在有效期内。');return}
    const url=URL.createObjectURL(await response.blob())
    const anchor=document.createElement('a')
    anchor.href=url;anchor.download=`${resultId}.csv`;anchor.click()
    URL.revokeObjectURL(url)
  }

  function newThread(){
    setThreadId(undefined);setMessages([]);setEvents([]);setResult(undefined);setResultId(undefined);setChartDsl(undefined);setInterrupt(undefined);setError(undefined)
    navigate('/app')
  }

  async function saveTimezone(value:string){
    try{
      setPreferences(await requestJson<UserPreferences>('/api/settings',token,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:'timezone',value,confirmed:true})}))
      setError(undefined)
    }catch(reason){setError(reason instanceof Error?reason.message:'偏好保存失败')}
  }

  const currentTitle=threadId?threadTitles[threadId]:undefined

  if(showSettings)return <main className="settings-page"><section className="settings-panel" aria-label="用户设置">
    <p className="settings-eyebrow">已确认的长期偏好</p>
    <h1>分析设置</h1>
    <label htmlFor="timezone">默认时区</label>
    <select id="timezone" value={String(preferences.values?.timezone??'Asia/Shanghai')} onChange={event=>void saveTimezone(event.target.value)}>
      <option>Asia/Shanghai</option><option>UTC</option>
      <option>America/New_York</option><option>Europe/London</option>
    </select>
    <p>显式查询条件始终优先于这里的默认值。</p>
    {error&&<p className="settings-error" role="alert">{error}</p>}
    <button className="settings-back" onClick={()=>navigate('/app')}>返回工作台</button>
  </section></main>

  return <div className="workbench">
    <Sidebar identity={identity} threads={threads} current={threadId} onOpen={id=>void openThread(id)} onNew={newThread} onLogout={logout} onSettings={()=>navigate('/app/settings')}/>
    <Conversation
      messages={messages}events={events}interrupt={interrupt}
      chartDsl={chartDsl}result={result}busy={busy}connection={connection}
      error={error}recommended={recommended}
      identity={identity}preferences={preferences}threadTitle={currentTitle}
      onSend={text=>void ask(text)}onPick={text=>{setQuestion(text)}}
      onResume={answer=>void resume(answer)}onStop={()=>streamAbort.current?.abort()}
      onDownload={()=>void downloadCsv()}/>
    {chartDsl&&result&&<div className="chart-floating"><Chart dsl={chartDsl} result={result}/></div>}
  </div>
}

export function Root(){
  const[token,setToken]=useState<string>()
  const expiry=tokenExpiry(token as string)
  useEffect(()=>{if(token&&expiry&&expiry.getTime()<=Date.now())setToken(undefined)},[token,expiry])
  return token?<Workbench token={token} logout={()=>setToken(undefined)}/>:<AuthPage onAuthenticated={setToken}/>
}

const rootElement=document.getElementById('root')
if(rootElement)createRoot(rootElement).render(<StrictMode><Root/></StrictMode>)