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
 it('keeps BBOX labels readable at a fixed screen size while zooming',async()=>{
  const view=render(<App/>);await waitFor(()=>expect(view.getByText('sample')).toBeTruthy())
  const stage=view.container.querySelector<HTMLElement>('.stage')!
  vi.spyOn(stage,'getBoundingClientRect').mockReturnValue({width:50,height:50} as DOMRect)
  fireEvent.resize(window)
  fireEvent.click(view.getByText('sample'));await waitFor(()=>expect(view.getByText('1.jpg')).toBeTruthy())
  const svg=stage.querySelector<SVGSVGElement>('svg')!
  const assertLabelSize=()=>{
   const visibleWidth=Number(svg.getAttribute('viewBox')!.split(' ')[2])
   const text=svg.querySelector('g text')!,background=svg.querySelectorAll('g rect')[1]
   expect(Number(text.getAttribute('font-size'))*50/visibleWidth).toBeCloseTo(20)
   expect(Number(background.getAttribute('height'))*50/visibleWidth).toBeCloseTo(30)
  }
  assertLabelSize()
  fireEvent.wheel(svg,{deltaY:-100,clientX:30,clientY:30})
  assertLabelSize()
 })
 it('starts zoomed in while keeping the annotation visible',async()=>{
  const view=render(<App/>);await waitFor(()=>expect(view.getByText('sample')).toBeTruthy())
  const stage=view.container.querySelector<HTMLElement>('.stage')!
  vi.spyOn(stage,'getBoundingClientRect').mockReturnValue({width:100,height:200} as DOMRect)
  fireEvent.resize(window)
  fireEvent.click(view.getByText('sample'));await waitFor(()=>expect(view.getByText('1.jpg')).toBeTruthy())
  const [x,y,width,height]=view.container.querySelector('.stage svg')!.getAttribute('viewBox')!.split(' ').map(Number)
  expect(width).toBeCloseTo(20.8)
  expect(height).toBeCloseTo(41.6)
  expect(x).toBeLessThanOrEqual(20)
  expect(x+width).toBeGreaterThanOrEqual(40)
  expect(y).toBeLessThanOrEqual(30)
  expect(y+height).toBeGreaterThanOrEqual(60)
 })
 it('resizes the tile palette by dragging the divider',async()=>{
  const view=await opened();const inspector=view.container.querySelector<HTMLElement>('.inspector')!;
  const palette=inspector.querySelector<HTMLElement>('.palette-panel')!;
  const divider=inspector.querySelector<HTMLElement>('.inspector-splitter')!;
  vi.spyOn(inspector,'getBoundingClientRect').mockReturnValue({height:500} as DOMRect)
  vi.spyOn(palette,'getBoundingClientRect').mockReturnValue({height:300} as DOMRect)
  fireEvent.pointerDown(divider,{clientY:100})
  fireEvent.pointerMove(window,{clientY:150})
  expect(palette.style.height).toBe('70%')
  fireEvent.pointerMove(window,{clientY:1000})
  expect(parseFloat(palette.style.height)).toBeCloseTo(76.8)
  fireEvent.pointerUp(window)
 })
 it('pans the zoomed image when dragging empty space',async()=>{
  const view=await opened(),svg=view.container.querySelector<SVGSVGElement>('.stage svg')!
  vi.spyOn(svg,'getBoundingClientRect').mockReturnValue({width:100,height:100} as DOMRect)
  const startX=Number(svg.getAttribute('viewBox')!.split(' ')[0])
  fireEvent.pointerDown(svg,{button:0,clientX:50,clientY:50,pointerId:1})
  fireEvent.pointerMove(svg,{clientX:70,clientY:50,pointerId:1})
  fireEvent.pointerUp(svg,{clientX:70,clientY:50,pointerId:1})
  expect(Number(svg.getAttribute('viewBox')!.split(' ')[0])).toBeLessThan(startX)
  expect(view.container.querySelectorAll('.annotation-row')).toHaveLength(1)
  expect(view.getByText('保存').closest('button')?.hasAttribute('disabled')).toBe(true)
 })
 it('creates one box in creation mode and returns to panning',async()=>{
  const view=await opened(),svg=view.container.querySelector<SVGSVGElement>('.stage svg')!
  const button=view.getByText('＋ BBOX作成')
  fireEvent.click(button)
  expect(button.getAttribute('aria-pressed')).toBe('true')
  fireEvent.pointerDown(svg.querySelector('g rect')!,{button:0,clientX:50,clientY:50,pointerId:1})
  fireEvent.pointerMove(svg,{clientX:60,clientY:65,pointerId:1})
  fireEvent.pointerUp(svg,{clientX:60,clientY:65,pointerId:1})
  expect(view.container.querySelectorAll('.annotation-row')).toHaveLength(2)
  expect(view.getByText('＋ BBOX作成').getAttribute('aria-pressed')).toBe('false')
  vi.spyOn(svg,'getBoundingClientRect').mockReturnValue({width:100,height:100} as DOMRect)
  fireEvent.pointerDown(svg,{button:0,clientX:50,clientY:50,pointerId:2})
  fireEvent.pointerMove(svg,{clientX:55,clientY:55,pointerId:2})
  fireEvent.pointerUp(svg,{clientX:55,clientY:55,pointerId:2})
  expect(view.container.querySelectorAll('.annotation-row')).toHaveLength(2)
 })
 it('selects and moves a box on the SVG image',async()=>{
  const view=await opened();const svg=view.container.querySelector('.stage svg')!;const rect=svg.querySelector('g rect')!;
  const list=view.container.querySelector<HTMLElement>('.annotation-list')!;
  const row=list.querySelector<HTMLElement>('.annotation-row')!;
  vi.spyOn(list,'getBoundingClientRect').mockReturnValue({top:100,bottom:200} as DOMRect)
  vi.spyOn(row,'getBoundingClientRect').mockReturnValue({top:230,bottom:258} as DOMRect)
  fireEvent.pointerDown(rect,{clientX:25,clientY:35,pointerId:1});fireEvent.pointerUp(svg,{clientX:25,clientY:35,pointerId:1});
  expect(view.container.querySelector('.annotation-row.selected')).toBeTruthy();
  expect(list.scrollTop).toBe(58)
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
