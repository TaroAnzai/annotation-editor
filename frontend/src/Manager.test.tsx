// @vitest-environment jsdom
import {afterEach,expect,it,vi} from 'vitest'
import {cleanup,fireEvent,render,waitFor} from '@testing-library/react'
import Manager from './Manager'

afterEach(()=>{cleanup();sessionStorage.removeItem('mahjong-annotation-editor-active-job');sessionStorage.removeItem('mahjong-annotation-editor-blocking-job');localStorage.removeItem('mahjong-annotation-editor-vps-connection');vi.unstubAllGlobals()})

it('disables protected batch deletion and reports a failed delete without removing the row',async()=>{
 const batches=[
  {id:1,name:'protected',deletable:false,delete_blockers:['バージョンに含まれる画像があります']},
  {id:2,name:'available',deletable:true,delete_blockers:[]},
 ].map(b=>({...b,format:'yolo',origin:'',image_count:1,annotation_count:1,bbox_count:1,source_types:{real:1},reviewed_count:0,unreviewed_count:1,held_count:0,duplicate_count:0}))
 const calls:string[]=[]
 vi.stubGlobal('confirm',vi.fn(()=>true))
 vi.stubGlobal('fetch',vi.fn(async(input:string,init?:RequestInit)=>{
  const url=String(input)
  if(init?.method==='DELETE'){calls.push(url);return {ok:false,status:409,statusText:'Conflict',json:async()=>({detail:'削除できません: バージョンに含まれる画像があります'})}}
  return {ok:true,json:async()=>url==='/api/manage/batches'?batches:url==='/api/manage/versions'?[]:{}}
 }))
 const view=render(<Manager onEditor={()=>{}}/>)
 const buttons=await view.findAllByRole('button',{name:'削除'}) as HTMLButtonElement[]
 expect(buttons[0].disabled).toBe(true)
 expect(view.getByText('バージョンに含まれる画像があります')).toBeTruthy()
 fireEvent.click(buttons[1])
 await waitFor(()=>expect(calls).toEqual(['/api/manage/batches/2']))
 await waitFor(()=>expect(view.getByText(/削除できません: バージョンに含まれる画像があります/)).toBeTruthy())
 expect(view.getByText('available')).toBeTruthy()
})

it('uses the next numeric ID by default and accepts a custom version ID and description',async()=>{
 const posts:any[]=[]
 vi.stubGlobal('fetch',vi.fn(async(input:string,init?:RequestInit)=>{
  const url=String(input)
  if(init?.method==='POST'){posts.push(JSON.parse(String(init.body)));return {ok:true,json:async()=>({job_id:'create-job'})}}
  return {ok:true,json:async()=>url==='/api/manage/versions'?[{id:'v007',parent_id:null,kind:'completed',description:'',created_at:'',test_id:null,status:'complete',upload_status:'not_uploaded',manifest_json:'{}',included_batch_ids:[]}]:url==='/api/manage/batches'?[]:{}}
 }))
 const view=render(<Manager onEditor={()=>{}}/>)
 fireEvent.click(view.getByText('バージョン管理'))
 const id=await view.findByLabelText('バージョンID') as HTMLInputElement
 expect(id.value).toBe('v008')
 fireEvent.change(id,{target:{value:'experiment_2026'}})
 fireEvent.change(view.getByLabelText('説明（任意）'),{target:{value:'評価用'}})
 fireEvent.click(view.getByText('未分割バージョン作成（TESTは後で設定）'))
 await waitFor(()=>expect(posts[0]).toMatchObject({version_id:'experiment_2026',description:'評価用'}))
})

it('asks for confirmation in a modal before deleting a saved version',async()=>{
 let present=true
 const calls:string[]=[]
 const saved={id:'v003',parent_id:null,kind:'unpartitioned',description:'検証用',created_at:'',test_id:null,status:'unpartitioned',upload_status:'not_uploaded',manifest_json:'{}',included_batch_ids:[],deletable:true,delete_blockers:[]}
 vi.stubGlobal('fetch',vi.fn(async(input:string,init?:RequestInit)=>{
  const url=String(input)
  if(init?.method==='DELETE'){calls.push(url);present=false;return {ok:true,json:async()=>({deleted:true})}}
  return {ok:true,json:async()=>url==='/api/manage/versions'?(present?[saved]:[]):url==='/api/manage/batches'?[]:{}}
 }))
 const view=render(<Manager onEditor={()=>{}}/>)
 fireEvent.click(view.getByText('バージョン管理'))
 const button=await view.findByRole('button',{name:'削除'})
 fireEvent.click(button)
 expect(calls).toEqual([])
 const dialog=view.getByRole('dialog',{name:'バージョン削除の確認'})
 expect(dialog.textContent).toContain('v003')
 fireEvent.click(view.getByRole('button',{name:'キャンセル'}))
 expect(view.queryByRole('dialog',{name:'バージョン削除の確認'})).toBeNull()
 fireEvent.click(button)
 fireEvent.click(view.getByRole('button',{name:'削除する'}))
 await waitFor(()=>expect(calls).toEqual(['/api/manage/versions/v003']))
 await waitFor(()=>expect(view.queryByText('v003')).toBeNull())
})

it('remembers a successful VPS connection and prefers it over backend defaults',async()=>{
 let reject=false
 vi.stubGlobal('fetch',vi.fn(async(input:string,init?:RequestInit)=>{
  const url=String(input)
  if(url==='/api/manage/vps/versions'&&init?.method==='POST'){
   if(reject)return {ok:false,status:422,statusText:'Unprocessable Entity',json:async()=>({detail:'接続できません'})}
   return {ok:true,json:async()=>({versions:[]})}
  }
  return {ok:true,json:async()=>url==='/api/manage/vps/defaults'?{host:'default-host',remote_root:'/default',source_root:'/data/sources'}:url==='/api/manage/batches'||url==='/api/manage/versions'?[]:{}}
 }))
 const first=render(<Manager onEditor={()=>{}}/>)
 fireEvent.click(first.getByText('VPSから取得'))
 await waitFor(()=>expect((first.getByLabelText('SSH接続先') as HTMLInputElement).value).toBe('default-host'))
 fireEvent.change(first.getByLabelText('SSH接続先'),{target:{value:'vps'}})
 fireEvent.change(first.getByLabelText('VPSの保存ルート'),{target:{value:'/srv/samples/'}})
 fireEvent.click(first.getByText('完成済みの版を一覧表示'))
 await waitFor(()=>expect(JSON.parse(localStorage.getItem('mahjong-annotation-editor-vps-connection')||'null')).toEqual({host:'vps',remote_root:'/srv/samples/'}))
 reject=true
 fireEvent.change(first.getByLabelText('SSH接続先'),{target:{value:'wrong-host'}})
 fireEvent.click(first.getByText('完成済みの版を一覧表示'))
 await waitFor(()=>expect(first.getByText(/接続できません/)).toBeTruthy())
 expect(JSON.parse(localStorage.getItem('mahjong-annotation-editor-vps-connection')||'null')).toEqual({host:'vps',remote_root:'/srv/samples/'})
 first.unmount()
 const second=render(<Manager onEditor={()=>{}}/>)
 fireEvent.click(second.getByText('VPSから取得'))
 await waitFor(()=>expect((second.getByLabelText('SSH接続先') as HTMLInputElement).value).toBe('vps'))
 expect((second.getByLabelText('VPSの保存ルート') as HTMLInputElement).value).toBe('/srv/samples/')
})

it('explains a missing VPS API instead of showing Not Found',async()=>{
 vi.stubGlobal('fetch',vi.fn(async(input:string)=>{
  const url=String(input)
  if(url==='/api/manage/vps/defaults')return {ok:false,status:404,statusText:'Not Found',json:async()=>({detail:'Not Found'})}
  return {ok:true,json:async()=>url==='/api/manage/batches'||url==='/api/manage/versions'?[]:{}}
 }))
 const view=render(<Manager onEditor={()=>{}}/>)
 await waitFor(()=>expect(view.getByText(/VPS用APIが見つかりません.*バックエンドを再起動してください/)).toBeTruthy())
})

it('lists completed VPS versions and imports the selected version into sources',async()=>{
 const posts:{url:string;body:any}[]=[]
 vi.stubGlobal('fetch',vi.fn(async(input:string,init?:RequestInit)=>{
  const url=String(input)
  if(init?.method==='POST'){
   const body=JSON.parse(String(init.body));posts.push({url,body})
   return {ok:true,json:async()=>url==='/api/manage/vps/versions'?{versions:['samples20261017-01','samples20261017-02']}:{job_id:'copy-job'}}
  }
  const value=url==='/api/manage/vps/defaults'?{host:'vps',remote_root:'/srv',source_root:'/data/sources'}:
   url==='/api/manage/batches'||url==='/api/manage/versions'?[]:
   url==='/api/manage/jobs/copy-job'?{kind:'vps_import',status:'complete',progress:5,total:5,result:'VPS版をsourcesへ登録しました: /data/sources/samples20261017-02'}:{}
  return {ok:true,json:async()=>value}
 }))
 const view=render(<Manager onEditor={()=>{}}/>)
 fireEvent.click(view.getByText('VPSから取得'))
 await waitFor(()=>expect((view.getByLabelText('SSH接続先') as HTMLInputElement).value).toBe('vps'))
 fireEvent.click(view.getByText('完成済みの版を一覧表示'))
 await waitFor(()=>expect(view.getByLabelText('取得する版を選択')).toBeTruthy())
 fireEvent.click(view.getByLabelText('取得する版を選択'))
 fireEvent.click(view.getByRole('button',{name:'samples20261017-02'}))
 expect(view.getByText('編集用バッチ: /data/sources/samples20261017-02')).toBeTruthy()
 fireEvent.click(view.getByText('選択した版を取得・sourcesへ登録'))
 await waitFor(()=>expect(posts[1]).toEqual({url:'/api/manage/vps/copy',body:{host:'vps',remote_root:'/srv',version:'samples20261017-02'}}))
 await waitFor(()=>expect(view.getByText('VPS版をsourcesへ登録しました: /data/sources/samples20261017-02')).toBeTruthy())
})

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
 expect(posts[0]).toEqual({url:'/api/manage/versions/from-reference',body:{parent:'v001',reference_path:'/example/dataset',description:'',version_id:'v002'}})
 expect(view.getByRole('dialog',{name:'バージョン作成の進捗'})).toBeTruthy()
 expect(view.container.querySelector('.manager-content')?.hasAttribute('inert')).toBe(true)
})

it('disables batches already in the parent and submits no additional batches when none remain selected',async()=>{
 const posts:{url:string;body:any}[]=[]
 const batches=[1,2].map(id=>({id,name:`batch-${id}`,format:'coco',origin:'',image_count:2,annotation_count:2,bbox_count:2,source_types:{real:2},reviewed_count:2,unreviewed_count:0,held_count:0,duplicate_count:0}))
 const versions=[1,2].map(id=>({id:`v00${id}`,parent_id:null,kind:'completed',description:'',created_at:'',test_id:null,status:'complete',upload_status:'not_uploaded',manifest_json:'{}',included_batch_ids:[id]}))
 vi.stubGlobal('fetch',vi.fn(async(input:string,init?:RequestInit)=>{
  const url=String(input)
  if(init?.method==='POST'){posts.push({url,body:JSON.parse(String(init.body))});return {ok:true,json:async()=>({job_id:'queued'})}}
  return {ok:true,json:async()=>url==='/api/manage/batches'?batches:url==='/api/manage/versions'?versions:url==='/api/manage/analysis'?{}:{ready:false,message:'未設定'}}
 }))
 const view=render(<Manager onEditor={()=>{}}/>)
 fireEvent.click(view.getByText('バージョン管理'))
 const first=await view.findByRole('checkbox',{name:/batch-1/}) as HTMLInputElement
 const second=view.getByRole('checkbox',{name:/batch-2/}) as HTMLInputElement
 fireEvent.click(second)
 fireEvent.click(view.getByLabelText('親バージョンを選択'))
 fireEvent.click(view.getByRole('button',{name:'v001 · 完成'}))
 expect(first.disabled).toBe(true)
 expect(second.checked).toBe(true)
 fireEvent.click(view.getByLabelText('親バージョンを選択'))
 fireEvent.click(view.getByRole('button',{name:'v002 · 完成'}))
 expect(first.disabled).toBe(false)
 expect(second.disabled).toBe(true)
 expect(second.checked).toBe(false)
 fireEvent.click(view.getByText('完成バージョン作成'))
 await waitFor(()=>expect(posts).toHaveLength(1))
 expect(posts[0].body).toMatchObject({parent:'v002',batch_ids:[]})
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

it('starts annotation to DB sync for the selected batch',async()=>{
 const calls:string[]=[]
 vi.stubGlobal('confirm',vi.fn(()=>true))
 vi.stubGlobal('fetch',vi.fn(async(input:string,init?:RequestInit)=>{
  const url=String(input)
  if(init?.method==='POST'){calls.push(url);return {ok:true,json:async()=>({job_id:'sync-job'})}}
  const value=url==='/api/manage/batches'?[{id:3,name:'synthetic',format:'coco',origin:'',image_count:2,annotation_count:2,bbox_count:2,source_types:{synthetic:2},reviewed_count:2,unreviewed_count:0,held_count:0,duplicate_count:0}]:
   url==='/api/manage/versions'?[]:url==='/api/manage/analysis'?{}:{kind:'sync_batch',status:'running',progress:0,total:2}
  return {ok:true,json:async()=>value}
 }))
 const view=render(<Manager onEditor={()=>{}}/>)
 const button=await view.findByRole('button',{name:'アノテーションでDBを更新'})
 fireEvent.click(button)
 await waitFor(()=>expect(calls).toEqual(['/api/manage/batches/3/sync']))
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

it('summarizes an older verbose version result and lets the user dismiss it',async()=>{
 vi.stubGlobal('fetch',vi.fn(async(input:string,init?:RequestInit)=>{
  const url=String(input)
  if(init?.method==='POST')return {ok:true,json:async()=>({job_id:'verbose-job'})}
  if(url==='/api/manage/jobs/verbose-job')return {ok:true,json:async()=>({kind:'version',status:'complete',progress:6,total:6,result:"{'id': 'v003', 'manifest': {'leaf_sources': ["+'x'.repeat(2000)+"]}"})}
  return {ok:true,json:async()=>url==='/api/manage/batches'?[]:url==='/api/manage/versions'?[]:url==='/api/manage/analysis'?{}:{ready:false,message:'未設定'}}
 }))
 const view=render(<Manager onEditor={()=>{}}/>)
 fireEvent.click(view.getByText('バージョン管理'))
 fireEvent.click(view.getByText('完成バージョン作成'))
 await waitFor(()=>expect(view.getByText('v003 を作成しました')).toBeTruthy())
 expect(view.queryByText(/version: complete/)).toBeNull()
 expect(view.container.querySelector('.manager-message')?.textContent).not.toContain('leaf_sources')
 fireEvent.click(view.getByRole('button',{name:'完了通知を閉じる'}))
 expect(view.queryByText('v003 を作成しました')).toBeNull()
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
