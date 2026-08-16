import {expect,it,vi} from 'vitest'
import {consumeSse,newClientRequestId} from './client'
import type {StreamEvent} from './types'

it('replays SSE after Last-Event-ID without dropping repeated graph nodes',async()=>{
  const payload=[
    `id: 8\nevent: node.started\ndata: {"event":"node.started","request_id":"r","thread_id":"t","status":"RUNNING","node":"agent_node","action":"RETRIEVE"}\n\n`,
    `id: 9\nevent: node.started\ndata: {"event":"node.started","request_id":"r","thread_id":"t","status":"RUNNING","node":"agent_node","action":"GENERATE"}\n\n`,
    `id: 10\nevent: run.completed\ndata: {"event":"run.completed","request_id":"r","thread_id":"t","status":"SUCCEEDED","result_ids":[],"artifact_ids":[]}\n\n`,
  ].join('')
  const fetchMock=vi.fn((_input:RequestInfo|URL,init?:RequestInit)=>Promise.resolve(new Response(new ReadableStream({start(controller){controller.enqueue(new TextEncoder().encode(payload));controller.close()}}),{status:200})))
  vi.stubGlobal('fetch',fetchMock);const cursor={lastEventId:7},events:number[]=[]
  await consumeSse({url:'/api/chat/stream',token:'token',requestId:'r',cursor,signal:new AbortController().signal,onEvent:event=>{events.push(event.eventId!)}})
  expect(events).toEqual([8,9,10]);expect(cursor.lastEventId).toBe(10);expect(fetchMock).toHaveBeenCalledWith('/api/chat/stream',expect.objectContaining({headers:expect.objectContaining({'Last-Event-ID':'7'})}))
})

it('processes a terminal SSE packet even when the connection omits the final blank line',async()=>{
  const fetchMock=vi.fn(()=>Promise.resolve(new Response(new ReadableStream({start(controller){controller.enqueue(new TextEncoder().encode(
    'id: 1\nevent: run.completed\ndata: {"event":"run.completed","request_id":"r","thread_id":"t","status":"SUCCEEDED","result_ids":[],"artifact_ids":[]}'
  ));controller.close()}}))))
  vi.stubGlobal('fetch',fetchMock);const events:StreamEvent[]=[]
  await consumeSse({url:'/api/chat/stream',token:'token',requestId:'r',cursor:{lastEventId:0},signal:new AbortController().signal,onEvent:event=>{events.push(event)}})
  expect(events).toHaveLength(1);expect(events[0].event).toBe('run.completed')
})

it('creates request ids when randomUUID is unavailable on a LAN HTTP origin',()=>{
  vi.stubGlobal('crypto',{})
  const first=newClientRequestId(),second=newClientRequestId()
  expect(first).toMatch(/^web-/);expect(second).not.toBe(first)
})
