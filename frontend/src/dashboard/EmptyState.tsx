import {RecommendedQuestions} from './RecommendedQuestions'

export function EmptyState({identity,recommended,onPick}:{identity?:{user_id:string};recommended:string[];onPick:(question:string)=>void}){
  const hour=new Date().getHours()
  const greeting=hour<6?'深夜好':hour<12?'早上好':hour<14?'中午好':hour<18?'下午好':'晚上好'
  const firstName=identity?.user_id?.split(/[_\-.]/)[0]??identity?.user_id
  return <section className="empty-state" aria-label="新会话">
    <p className="empty-eyebrow">新会话</p>
    <h1 className="empty-headline">Hi <em>{firstName}</em>，<em>{greeting}</em></h1>
    <p className="empty-sub">挑一个问题开始，或直接在下方输入自己的问题。</p>
    <RecommendedQuestions items={recommended} onPick={onPick}/>
  </section>
}