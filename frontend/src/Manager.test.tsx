// @vitest-environment jsdom
import {afterEach,expect,it,vi} from 'vitest'
import {cleanup,fireEvent,render,waitFor} from '@testing-library/react'
import Manager from './Manager'

afterEach(()=>{cleanup();sessionStorage.removeItem('mahjong-annotation-editor-active-job');sessionStorage.removeItem('mahjong-annotation-editor-blocking-job');vi.unstubAllGlobals()})

it('creates a version from a read-only split reference with the selected parent',async()=>{
 const posts:{url:string;body:any}[]=[]
 vi.stubGlobal('fetch',vi.fn(async(input:string,init?:RequestInit)=>{
  const url=String(input)
  if(init?.method==='POST'){
   posts.push({url,body:JSON.parse(String(init.body))})
   return {ok:true,json:async()=>({job_id:'queued'})}
  }
  const value=url==='/api/manage/batches'?[]:
   url==='/api/manage/versions'?[{id:'v001',parent_id:null,kind:'unpartitioned',description:'',created_at:'',test_id:null,status:'unpartitioned',upload_status:'not_uploaded',manifest_json:'{}'}]:
   url==='/api/manage/analysis'?{}:
   url==='/api/manage/drive/setup'?{ready:false,message:'未設定'}:{id:'queued',status:'running',progress:0,total:6}
  return {ok:true,json:async()=>value}
 }))
 const view=render(<Manager onEditor={()=>{}}/>)
 fireEvent.click(view.getByText('バージョン管理'))
 await waitFor(()=>expect(view.getByText('既存datasetと同じ分割で新規作成')).toBeTruthy())
 const parentDropdown=view.getByLabelText('親バージョンを選択') as HTMLElement
 expect(parentDropdown.closest('details')?.open).toBe(false)
 fireEvent.click(parentDropdown)
 expect(parentDropdown.closest('details')?.open).toBe(true)
 fireEvent.click(view.getByRole('button',{name:'v001 · 未分割'}))
 expect(parentDropdown.closest('details')?.open).toBe(false)
 expect(parentDropdown.textContent).toBe('v001 · 未分割')
 fireEvent.click(parentDropdown)
 fireEvent.pointerDown(view.getByText('新しいバージョンを作成'))
 expect(parentDropdown.closest('details')?.open).toBe(false)
 fireEvent.click(parentDropdown)
 fireEvent.keyDown(document,{key:'Escape'})
 expect(parentDropdown.closest('details')?.open).toBe(false)
 fireEvent.change(view.getByLabelText('参照datasetフォルダー'),{target:{value:'/example/dataset'}})
 fireEvent.click(view.getByText('同じtrain / val / testで完成バージョン作成'))
 await waitFor(()=>expect(posts).toHaveLength(1))
 expect(posts[0]).toEqual({url:'/api/manage/versions/from-reference',body:{parent:'v001',reference_path:'/example/dataset',description:''}})
 expect(view.getByRole('dialog',{name:'バージョン作成の進捗'})).toBeTruthy()
 expect(view.container.querySelector('.manager-content')?.hasAttribute('inert')).toBe(true)
})

it('uses in-page dropdowns for import settings and sends the selected values to preview',async()=>{
 const posts:{url:string;body:any}[]=[]
 vi.stubGlobal('fetch',vi.fn(async(input:string,init?:RequestInit)=>{
  const url=String(input)
  if(init?.method==='POST'){
   posts.push({url,body:JSON.parse(String(init.body))})
   return {ok:true,json:async()=>({kind:'prepared',image_count:0,annotation_count:0,bbox_count:0,duplicates:0,conflicts:[],errors:[],preview_id:'preview',manifest:{}})}
  }
  return {ok:true,json:async()=>url==='/api/manage/batches'?[]:url==='/api/manage/versions'?[]:{}}
 }))
 const view=render(<Manager onEditor={()=>{}}/>)
 fireEvent.click(view.getByText('データ取り込み'))
 expect(view.container.querySelector('select')).toBeNull()
 const modeDropdown=view.getByLabelText('取り込み方法を選択')
 const sourceDropdown=view.getByLabelText('画像種類（画像に情報がない場合）を選択')
 expect(modeDropdown.textContent).toBe('コピー')
 expect(sourceDropdown.textContent).toBe('実写')
 fireEvent.click(modeDropdown)
 fireEvent.click(view.getByRole('button',{name:'移動（登録後に元を削除）'}))
 expect(modeDropdown.closest('details')?.open).toBe(false)
 fireEvent.click(sourceDropdown)
 fireEvent.click(view.getByRole('button',{name:'画像内のsource_typeを使用'}))
 expect(sourceDropdown.closest('details')?.open).toBe(false)
 expect(modeDropdown.textContent).toBe('移動（登録後に元を削除）')
 expect(sourceDropdown.textContent).toBe('画像内のsource_typeを使用')
 fireEvent.click(view.getByText('検証・プレビュー'))
 await waitFor(()=>expect(posts).toHaveLength(1))
 expect(posts[0]).toEqual({url:'/api/manage/import/preview',body:{path:'',mode:'move',source_type:null,name:'',origin:''}})
})

it('uses the selected folder name as the batch default and preserves manual edits',async()=>{
 const posts:{url:string;body:any}[]=[]
 vi.stubGlobal('fetch',vi.fn(async(input:string,init?:RequestInit)=>{
  const url=String(input)
  if(init?.method==='POST'){
   posts.push({url,body:JSON.parse(String(init.body))})
   return {ok:true,json:async()=>({path:['/chosen/dataset',null,'/chosen/next/','/chosen/third'][posts.length-1]})}
  }
  return {ok:true,json:async()=>url==='/api/manage/batches'?[]:url==='/api/manage/versions'?[]:{}}
 }))
 const view=render(<Manager onEditor={()=>{}}/>)
 fireEvent.click(view.getByText('データ取り込み'))
 const input=view.getByLabelText('元フォルダー') as HTMLInputElement
 const batchName=view.getByLabelText('バッチ名') as HTMLInputElement
 fireEvent.change(input,{target:{value:'/old/dataset'}})
 fireEvent.click(input)
 await waitFor(()=>expect(input.value).toBe('/chosen/dataset'))
 expect(batchName.value).toBe('dataset')
 expect(posts[0]).toEqual({url:'/api/manage/import/select-folder',body:{current_path:'/old/dataset'}})
 fireEvent.click(view.getByRole('button',{name:'参照…'}))
 await waitFor(()=>expect(posts).toHaveLength(2))
 expect(input.value).toBe('/chosen/dataset')
 expect(batchName.value).toBe('dataset')
 fireEvent.click(view.getByRole('button',{name:'参照…'}))
 await waitFor(()=>expect(input.value).toBe('/chosen/next/'))
 expect(batchName.value).toBe('next')
 fireEvent.change(batchName,{target:{value:'custom-batch'}})
 fireEvent.click(view.getByRole('button',{name:'参照…'}))
 await waitFor(()=>expect(input.value).toBe('/chosen/third'))
 expect(batchName.value).toBe('custom-batch')
})

it('allows a duplicate image but explains an annotation conflict',async()=>{
 let conflict=false
 vi.stubGlobal('fetch',vi.fn(async(input:string,init?:RequestInit)=>{
  const url=String(input)
  if(init?.method==='POST')return {ok:true,json:async()=>({kind:'yolo',image_count:2,import_count:conflict?1:2,import_annotation_count:conflict?1:2,import_bbox_count:conflict?1:2,annotation_count:2,bbox_count:2,duplicates:1,conflicts:conflict?['images/duplicate.png']:[],errors:[],preview_id:'preview',manifest:{}})}
  return {ok:true,json:async()=>url==='/api/manage/batches'?[]:url==='/api/manage/versions'?[]:{}}
 }))
 const view=render(<Manager onEditor={()=>{}}/>)
 fireEvent.click(view.getByText('データ取り込み'))
 fireEvent.change(view.getByLabelText('バッチ名'),{target:{value:'new-batch'}})
 fireEvent.click(view.getByText('検証・プレビュー'))
 await waitFor(()=>expect(view.getByText(/annotationが一致する重複画像は既存の画像として登録します/)).toBeTruthy())
 expect((view.getByText('この内容を登録') as HTMLButtonElement).disabled).toBe(false)
 conflict=true
 fireEvent.click(view.getByText('検証・プレビュー'))
 await waitFor(()=>expect(view.getByText('annotation競合: images/duplicate.png')).toBeTruthy())
 expect(view.getByText(/競合 1 件を除外し、画像 1 枚 \/ annotation 1 件 \/ BBox 1 件を登録します/)).toBeTruthy()
 expect((view.getByText('この内容を登録') as HTMLButtonElement).disabled).toBe(false)
})

it('uses POST for batch confirmation without a request body',async()=>{
 const calls:{url:string;method:string;body:any}[]=[]
 vi.stubGlobal('confirm',vi.fn(()=>true))
 vi.stubGlobal('fetch',vi.fn(async(input:string,init?:RequestInit)=>{
  const url=String(input)
  if(init?.method){calls.push({url,method:init.method,body:JSON.parse(String(init.body))});return {ok:true,json:async()=>({job_id:'queued'})}}
  const value=url==='/api/manage/batches'?[{id:3,name:'synthetic',format:'coco',origin:'',image_count:2,annotation_count:2,bbox_count:2,source_types:{synthetic:2},reviewed_count:0,unreviewed_count:2,held_count:0,duplicate_count:0}]:
   url==='/api/manage/versions'?[]:url==='/api/manage/analysis'?{}:{id:'queued',status:'running',progress:0,total:2}
  return {ok:true,json:async()=>value}
 }))
 const view=render(<Manager onEditor={()=>{}}/>)
 await waitFor(()=>expect(view.getByText('全件確認済み')).toBeTruthy())
 fireEvent.click(view.getByText('全件確認済み'))
 await waitFor(()=>expect(calls).toHaveLength(1))
 expect(calls[0]).toEqual({url:'/api/manage/batches/3/confirm',method:'POST',body:{}})
})

it('resumes a running job after leaving and returning to data management',async()=>{
 vi.stubGlobal('confirm',vi.fn(()=>true))
 let polls=0
 let finish=false
 vi.stubGlobal('fetch',vi.fn(async(input:string,init?:RequestInit)=>{
  const url=String(input)
  if(init?.method==='POST')return {ok:true,json:async()=>({job_id:'continued-job'})}
  if(url==='/api/manage/jobs/continued-job'){
   polls++
   return {ok:true,json:async()=>({kind:'confirm_batch',status:finish?'complete':'running',progress:finish?2:1,total:2,result:'確認完了'})}
  }
  const value=url==='/api/manage/batches'?[{id:3,name:'synthetic',format:'coco',origin:'',image_count:2,annotation_count:2,bbox_count:2,source_types:{synthetic:2},reviewed_count:0,unreviewed_count:2,held_count:0,duplicate_count:0}]:
   url==='/api/manage/versions'?[]:url==='/api/manage/analysis'?{}:{ready:false,message:'未設定'}
  return {ok:true,json:async()=>value}
 }))
 const first=render(<Manager onEditor={()=>{}}/>)
 await waitFor(()=>expect(first.getByText('全件確認済み')).toBeTruthy())
 fireEvent.click(first.getByText('全件確認済み'))
 await waitFor(()=>expect(sessionStorage.getItem('mahjong-annotation-editor-active-job')).toBe('continued-job'))
 first.unmount()
 finish=true
 const second=render(<Manager onEditor={()=>{}}/>)
 await waitFor(()=>expect(second.getByText('確認完了')).toBeTruthy(),{timeout:2500})
 expect(polls).toBeGreaterThanOrEqual(1)
 expect(sessionStorage.getItem('mahjong-annotation-editor-active-job')).toBeNull()
})

it.each([
 ['完成バージョン作成','version',6],
 ['未分割バージョン作成（TESTは後で設定）','unpartitioned',3],
])('blocks app operations while %s runs and releases them on completion',async(button,kind,total)=>{
 let finish=false
 const onEditor=vi.fn()
 vi.stubGlobal('fetch',vi.fn(async(input:string,init?:RequestInit)=>{
  const url=String(input)
  if(init?.method==='POST')return {ok:true,json:async()=>({job_id:'version-job'})}
  if(url==='/api/manage/jobs/version-job')return {ok:true,json:async()=>({kind,status:finish?'complete':'running',progress:finish?total:1,total,result:'作成完了'})}
  return {ok:true,json:async()=>url==='/api/manage/batches'?[]:url==='/api/manage/versions'?[]:url==='/api/manage/analysis'?{}:{ready:false,message:'未設定'}}
 }))
 const view=render(<Manager onEditor={onEditor}/> )
 fireEvent.click(view.getByText('バージョン管理'))
 fireEvent.click(view.getByText(button))
 await waitFor(()=>expect(view.getByRole('dialog',{name:'バージョン作成の進捗'})).toBeTruthy())
 await waitFor(()=>expect(view.getByText(`1 / ${total} ステップ`)).toBeTruthy())
 expect(view.container.querySelector('.manager-content')?.hasAttribute('inert')).toBe(true)
 expect(sessionStorage.getItem('mahjong-annotation-editor-blocking-job')).toBe('1')
 const tabs=view.container.querySelector('.manager-tabs') as HTMLElement
 expect(tabs.closest('[inert]')).toBeTruthy()
 expect(onEditor).not.toHaveBeenCalled()
 finish=true
 await waitFor(()=>expect(view.queryByRole('dialog',{name:'バージョン作成の進捗'})).toBeNull(),{timeout:2500})
 expect(view.container.querySelector('.manager-content')?.hasAttribute('inert')).toBe(false)
 expect(sessionStorage.getItem('mahjong-annotation-editor-blocking-job')).toBeNull()
 expect(view.getByText('作成完了')).toBeTruthy()
})

it('restores the blocking modal for a running version job after remount',async()=>{
 sessionStorage.setItem('mahjong-annotation-editor-active-job','ongoing')
 sessionStorage.setItem('mahjong-annotation-editor-blocking-job','1')
 vi.stubGlobal('fetch',vi.fn(async(input:string)=>{
  const url=String(input)
  return {ok:true,json:async()=>url==='/api/manage/jobs/ongoing'?{kind:'reference',status:'running',progress:3,total:6}:url==='/api/manage/batches'?[]:url==='/api/manage/versions'?[]:url==='/api/manage/analysis'?{}:{ready:false,message:'未設定'}}
 }))
 const view=render(<Manager onEditor={()=>{}}/>)
 expect(view.getByRole('dialog',{name:'バージョン作成の進捗'})).toBeTruthy()
 await waitFor(()=>expect(view.getByText('3 / 6 ステップ')).toBeTruthy())
 expect(view.container.querySelector('.manager-content')?.hasAttribute('inert')).toBe(true)
})

it('releases the modal and shows the backend error when version creation fails',async()=>{
 vi.stubGlobal('fetch',vi.fn(async(input:string,init?:RequestInit)=>{
  const url=String(input)
  if(init?.method==='POST')return {ok:true,json:async()=>({job_id:'failed-version'})}
  if(url==='/api/manage/jobs/failed-version')return {ok:true,json:async()=>({kind:'unpartitioned',status:'failed',progress:1,total:3,error:'画像が破損しています'})}
  return {ok:true,json:async()=>url==='/api/manage/batches'?[]:url==='/api/manage/versions'?[]:url==='/api/manage/analysis'?{}:{ready:false,message:'未設定'}}
 }))
 const view=render(<Manager onEditor={()=>{}}/>)
 fireEvent.click(view.getByText('バージョン管理'))
 fireEvent.click(view.getByText('未分割バージョン作成（TESTは後で設定）'))
 await waitFor(()=>expect(view.getByText('画像が破損しています')).toBeTruthy())
 expect(view.queryByRole('dialog',{name:'バージョン作成の進捗'})).toBeNull()
 expect(view.container.querySelector('.manager-content')?.hasAttribute('inert')).toBe(false)
 expect(sessionStorage.getItem('mahjong-annotation-editor-blocking-job')).toBeNull()
})
