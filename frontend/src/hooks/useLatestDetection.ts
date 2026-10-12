import {useEffect,useState} from 'react'
import {DetectionResult,getLatestDetection} from '../api/videoApi'
export function useLatestDetection(enabled:boolean,epoch=0,sessionId?:string|null):{result:DetectionResult|null;fetchError:string|null} {
 const [stored,setStored]=useState<{epoch:number;sessionId?:string|null;value:DetectionResult|null;error:string|null}>({epoch:-1,value:null,error:null})
 useEffect(()=>{
  let active=true,timer:number|undefined,controller:AbortController|undefined
  const poll=async()=>{
   controller=new AbortController()
   try{const value=await getLatestDetection(controller.signal);if(!active)return
    if(sessionId!==undefined&&(!sessionId||value?.source_session_id!==sessionId)){setStored({epoch,sessionId,value:null,error:null});return}
    setStored({epoch,sessionId,value,error:null})
   }catch{if(active)setStored({epoch,sessionId,value:null,error:'Status belum dapat dimuat.'})}
   finally{if(active)timer=window.setTimeout(poll,500)}
  }
  if(enabled)void poll()
  return()=>{active=false;controller?.abort();if(timer!==undefined)window.clearTimeout(timer)}
 },[enabled,epoch,sessionId])
 if(!enabled||stored.epoch!==epoch||stored.sessionId!==sessionId)return {result:null,fetchError:null}
 return {result:stored.value,fetchError:stored.error}
}
