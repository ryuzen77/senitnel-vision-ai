import {act,cleanup,renderHook} from '@testing-library/react'
import {afterEach,beforeEach,it,expect,vi} from 'vitest'
import {useCurrentDomain} from './useCurrentDomain'
beforeEach(()=>vi.useFakeTimers());afterEach(()=>{cleanup();vi.unstubAllGlobals();vi.useRealTimers()})
it('rejects domain A when video status pins B and clears at render epoch change',async()=>{vi.stubGlobal('fetch',vi.fn().mockResolvedValue({ok:true,json:async()=>({source_session_id:'a',domain:'RETAIL_STORE'})}));const h=renderHook(({id,epoch})=>useCurrentDomain(true,epoch,id),{initialProps:{id:'b',epoch:1}});await act(async()=>{await vi.advanceTimersByTimeAsync(0)});expect(h.result.current.snapshot).toBeNull();h.rerender({id:'c',epoch:2});expect(h.result.current.snapshot).toBeNull()})
