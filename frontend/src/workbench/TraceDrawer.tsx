export function TraceDrawer({
  traceId,
  errorCode,
  requestId,
}: {
  traceId?: string
  errorCode?: string
  requestId?: string
}) {
  if (!traceId && !errorCode && !requestId) return null
  return (
    <details className="trace-drawer">
      <summary>排查信息</summary>
      <dl>
        {traceId && (
          <>
            <dt>trace_id</dt>
            <dd>{traceId}</dd>
          </>
        )}
        {requestId && (
          <>
            <dt>request_id</dt>
            <dd>{requestId}</dd>
          </>
        )}
        {errorCode && (
          <>
            <dt>error_code</dt>
            <dd>{errorCode}</dd>
          </>
        )}
      </dl>
    </details>
  )
}
