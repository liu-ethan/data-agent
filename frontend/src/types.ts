import type {components} from './api/schema'

export type ChatResult = components['schemas']['ChatResponse']
export type Interrupt = components['schemas']['Interrupt']
export type ResultPage = components['schemas']['ResultPage']
export type ThreadSummary = components['schemas']['ThreadSummary']
export type ThreadDetail = components['schemas']['ThreadDetail']
export type ArtifactRecord = components['schemas']['ArtifactRecord']
export type Identity = components['schemas']['IdentityResponse']
export type UserPreferences = components['schemas']['UserPreferences']
export type RuntimeEvent = components['schemas']['RuntimeEvent']
export type Action = components['schemas']['Action']
export type Message = {role:'user'|'assistant'|'system';content:string;created_at?:string}
export type ChartDsl = {type:'bar'|'line'|'horizontal_bar';result_id:string;category_field:string;value_field:string}
export type StreamEvent = RuntimeEvent & {eventId?:number}
