// @vitest-environment jsdom
import {afterEach,expect,it,vi} from 'vitest'
import {cleanup,fireEvent,render,waitFor} from '@testing-library/react'
import Augmentation from './Augmentation'

afterEach(()=>{cleanup();vi.unstubAllGlobals()})

it('previews the configured transformation and starts a generation job',async()=>{
 const posts:{url:string;body:any}[]=[]
 const defaults={brightness:{enabled:true,min:.8,max:1.2,probability:1},contrast:{enabled:true,min:.9,max:1.1,probability:1},rotation:{enabled:true,min:-3,max:3,probability:1}}
 vi.stubGlobal('fetch',vi.fn(async(input:string,init?:RequestInit)=>{
  const url=String(input)
  if(init?.method==='POST'){
   const body=JSON.parse(String(init.body));posts.push({url,body})
   return {ok:true,json:async()=>url.endsWith('/preview')?{original:'data:image/png;base64,AAAA',augmented:'data:image/png;base64,BBBB',params:{brightness:1,contrast:null,rotation:2},warnings:[],image:'source.png',index:0,total:1,boxes:[]}:{job_id:'aug-job'}}
  }
  const value=url.endsWith('/sources')?[{id:'abc',name:'source',path:'sources/source',images:1},{id:'other',name:'other',path:'sources/other',images:2}]:url.endsWith('/defaults')?defaults:{status:'complete',progress:1,total:1,result:'aug を作成しました'}
  return {ok:true,json:async()=>value}
 }))
 const onComplete=vi.fn();const view=render(<Augmentation onComplete={onComplete}/>);
 await waitFor(()=>expect(view.getByLabelText('sources/source（1枚）')).toBeTruthy())
 expect(view.container.querySelector('select')).toBeNull()
 fireEvent.click(view.getByLabelText('sources/source（1枚）'))
 fireEvent.click(view.getByLabelText('sources/other（2枚）'))
 fireEvent.change(view.getByLabelText('出力データセット名'),{target:{value:'aug'}})
 fireEvent.change(view.getByLabelText('乱数seed'),{target:{value:'7'}})
 fireEvent.click(view.getByRole('button',{name:'プレビュー更新'}))
 await view.findByAltText('拡張画像と変換後BBox')
 expect(posts[0].body.seed).toBe(7)
 expect(posts[0].body.dataset_ids).toEqual(['abc','other'])
 fireEvent.click(view.getByRole('button',{name:'新しいデータセットを生成'}))
 await waitFor(()=>expect(onComplete).toHaveBeenCalledOnce())
 expect(posts[1].body.name).toBe('aug')
 expect(posts[1].body.dataset_ids).toEqual(['abc','other'])
 expect(view.getByText(/aug を作成しました/)).toBeTruthy()
})
