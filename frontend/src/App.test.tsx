// @vitest-environment jsdom
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest'
import {render,fireEvent,waitFor,cleanup} from '@testing-library/react'
import App from './App'

const rows=[{id:'img1',name:'1.jpg',status:'unreviewed',modified:false,label_exists:true,bbox_count:1,label_error:false},{id:'img2',name:'2.jpg',status:'unreviewed',modified:false,label_exists:true,bbox_count:1,label_error:false}]
const box={class_id:0,x:.2,y:.3,w:.2,h:.3}
let writes:{url:string;body:any}[]=[]
beforeEach(()=>{
 writes=[]
 Object.defineProperty(SVGSVGElement.prototype,'createSVGPoint',{configurable:true,value:()=>({x:0,y:0,matrixTransform(){return {x:this.x,y:this.y}}})})
 Object.defineProperty(SVGSVGElement.prototype,'getScreenCTM',{configurable:true,value:()=>({inverse:()=>({})})})
 Object.defineProperty(SVGSVGElement.prototype,'setPointerCapture',{configurable:true,value:()=>{}})
 Object.defineProperty(SVGSVGElement.prototype,'releasePointerCapture',{configurable:true,value:()=>{}})
 vi.stubGlobal('fetch',vi.fn(async(input:string,init?:RequestInit)=>{
  const url=String(input),body=init?.body?JSON.parse(String(init.body)):null
  if(init?.method==='PUT'){
   writes.push({url,body})
   const data=url.endsWith('/annotations')?{boxes:body.boxes,version:'v2'}:{status:body.status,modified:false}
   return {ok:true,json:async()=>data}
  }
  const data=url==='/api/folders'?{id:'root',name:'data',path:'.',children:[{id:'folder',name:'sample',path:'sample',children:[]}]}:
   url==='/api/datasets'?[{id:'ds',name:'sample',path:'sample',images:2,editable:true}]:
   url.endsWith('/stats')?{reviewed:0,images:2}:url.endsWith('/images')?rows:
   url.startsWith('/api/images/')?{id:url.split('/')[3],name:url.includes('img1')?'1.jpg':'2.jpg',width:100,height:100,editable:true,classes:['1m','2m'],status:{status:'unreviewed',modified:false},annotations:{boxes:[{...box}],version:'v1',error:null}}:null
  return {ok:true,json:async()=>data}
 }))
})
afterEach(()=>{cleanup();vi.unstubAllGlobals()})
async function opened(){const view=render(<App/>);await waitFor(()=>expect(view.getByText('sample')).toBeTruthy());fireEvent.click(view.getByText('sample'));await waitFor(()=>expect(view.getByText('1.jpg')).toBeTruthy());return view}

describe('image editing workflow',()=>{
 it('selects and moves a box on the SVG image',async()=>{
  const view=await opened();const svg=view.container.querySelector('.stage svg')!;const rect=svg.querySelector('g rect')!;
  fireEvent.pointerDown(rect,{clientX:25,clientY:35,pointerId:1});fireEvent.pointerUp(svg,{clientX:25,clientY:35,pointerId:1});
  expect(view.container.querySelector('.annotation-row.selected')).toBeTruthy();
  fireEvent.pointerDown(rect,{clientX:25,clientY:35,pointerId:1});fireEvent.pointerMove(svg,{clientX:35,clientY:45,pointerId:1});fireEvent.pointerUp(svg,{clientX:35,clientY:45,pointerId:1});
  expect(Number(svg.querySelector('g rect')?.getAttribute('x'))).toBeCloseTo(30);
 })
 it('selects a box, changes only its class, and supports undo/redo',async()=>{
  const view=await opened();fireEvent.click(view.container.querySelector('.annotation-row')!);const rect=view.container.querySelector('svg g rect')!;const coords=[rect.getAttribute('x'),rect.getAttribute('y'),rect.getAttribute('width'),rect.getAttribute('height')]
  fireEvent.click([...view.container.querySelectorAll('.tile-grid button')].find(b=>b.textContent==='2m')!);
  expect(view.container.querySelector('.annotation-row strong')?.textContent).toBe('2m')
  expect([rect.getAttribute('x'),rect.getAttribute('y'),rect.getAttribute('width'),rect.getAttribute('height')]).toEqual(coords)
  fireEvent.keyDown(window,{key:'z',ctrlKey:true});expect(view.container.querySelector('.annotation-row strong')?.textContent).toBe('1m')
  fireEvent.keyDown(window,{key:'y',ctrlKey:true});expect(view.container.querySelector('.annotation-row strong')?.textContent).toBe('2m')
 })
 it('warns before changing images and can discard edits',async()=>{
  const view=await opened();fireEvent.click(view.container.querySelector('.annotation-row')!);fireEvent.click([...view.container.querySelectorAll('.tile-grid button')].find(b=>b.textContent==='2m')!);
  fireEvent.click(view.getByText('次の画像 →'));expect(view.getByText('未保存の変更があります')).toBeTruthy();expect(view.getByText('1.jpg')).toBeTruthy()
  fireEvent.click(view.getByText('破棄して続行'));await waitFor(()=>expect(view.getByText('2.jpg')).toBeTruthy());expect(writes).toHaveLength(0)
 })
 it('saves and marks reviewed before advancing',async()=>{
  const view=await opened();fireEvent.click(view.container.querySelector('.annotation-row')!);fireEvent.click([...view.container.querySelectorAll('.tile-grid button')].find(b=>b.textContent==='2m')!);
  fireEvent.click(view.getByText('確認済み・次へ'));await waitFor(()=>expect(view.getByText('2.jpg')).toBeTruthy());expect(writes.map(w=>w.url)).toEqual(['/api/images/img1/annotations','/api/images/img1/status']);expect(writes[0].body.boxes[0]).toEqual({...box,class_id:1})
 })
})
