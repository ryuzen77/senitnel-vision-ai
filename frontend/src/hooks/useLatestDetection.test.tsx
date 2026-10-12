import {act,cleanup,renderHook} from '@testing-library/react'
import {afterEach,beforeEach,it,expect,vi} from 'vitest'
import {useLatestDetection} from './useLatestDetection'
beforeEach(()=>vi.useFakeTimers());afterEach(()=>{cleanup();vi.unstubAllGlobals();vi.useRealTimers()})
it('does not publish a late A frame under the current B status identity',async()=>{const replies:((v:unknown)=>void)[]=[];vi.stubGlobal('fetch',vi.fn(()=>new Promise(resolve=>replies.push(v=>resolve({ok:true,status:200,json:async()=>v})))));const h=renderHook(({id,epoch})=>useLatestDetection(true,epoch,id),{initialProps:{id:'a',epoch:1}});h.rerender({id:'b',epoch:2});await act(async()=>{replies[0]({source_session_id:'a',detections:[]});await vi.advanceTimersByTimeAsync(0)});expect(h.result.current.result).toBeNull();await act(async()=>{replies[1]({source_session_id:'b',detections:[]});await vi.advanceTimersByTimeAsync(0)});expect(h.result.current.result?.source_session_id).toBe('b')})
