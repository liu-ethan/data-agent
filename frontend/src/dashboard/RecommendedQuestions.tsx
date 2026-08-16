export function RecommendedQuestions({items,onPick}:{items:string[];onPick:(question:string)=>void}){
  return <div className="recommended" aria-label="推荐问题">
    {items.slice(0,10).map(question=><button type="button" key={question} className="recommended-card" onClick={()=>onPick(question)}>
      <span className="recommended-mark" aria-hidden>→</span>
      <span className="recommended-text">{question}</span>
    </button>)}
  </div>
}