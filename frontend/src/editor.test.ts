import {describe,it,expect} from 'vitest'
import {ordered,category,fitBoxes,placeBoxLabel,viewSize,groupedClasses,tileName,clamp} from './editor'
describe('editor utilities',()=>{
 it('places BBOX labels above, right, left, then below before allowing overlap above',()=>{
  const main={class_id:0,x:.4,y:.4,w:.1,h:.1}
  const blockers=[
   {class_id:0,x:.4,y:.3,w:.1,h:.1},
   {class_id:0,x:.5,y:.4,w:.2,h:.1},
   {class_id:0,x:.2,y:.4,w:.2,h:.1},
   {class_id:0,x:.4,y:.5,w:.1,h:.1},
  ]
  const expected=['top','right','left','bottom','top']
  expected.forEach((side,count)=>expect(placeBoxLabel([main,...blockers.slice(0,count)],0,100,100,20,10).side).toBe(side))
  expect(placeBoxLabel([{class_id:0,x:.4,y:.02,w:.1,h:.1}],0,100,100,20,10).side).toBe('right')
 })
 it('allows slight overlap at the edge of a label',()=>{
  const main={class_id:0,x:.4,y:.4,w:.1,h:.1}
  const slight={class_id:0,x:.4,y:.29,w:.1,h:.02}
  const deeper={class_id:0,x:.4,y:.29,w:.1,h:.05}
  expect(placeBoxLabel([main,slight],0,100,100,20,10).side).toBe('top')
  expect(placeBoxLabel([main,deeper],0,100,100,20,10).side).toBe('right')
 })
 it('fits every box at the largest initial zoom',()=>{
  const boxes=[{class_id:0,x:.1,y:.2,w:.1,h:.2},{class_id:1,x:.7,y:.6,w:.1,h:.2}]
  const {zoom,view}=fitBoxes(boxes,1000,500)
  expect(zoom).toBeCloseTo(1/(.7*1.04))
  for(const box of boxes){
   expect(box.x*1000).toBeGreaterThanOrEqual(view.x-1e-8)
   expect((box.x+box.w)*1000).toBeLessThanOrEqual(view.x+1000/zoom+1e-8)
   expect(box.y*500).toBeGreaterThanOrEqual(view.y-1e-8)
   expect((box.y+box.h)*500).toBeLessThanOrEqual(view.y+500/zoom+1e-8)
  }
  expect(fitBoxes([],1000,500)).toEqual({zoom:1,view:{x:0,y:0}})
  expect(fitBoxes([{class_id:0,x:.5,y:.5,w:.01,h:.01}],1000,500).zoom).toBeGreaterThan(8)
 })
 it('uses the display area to fill its width when height allows',()=>{
  const boxes=[{class_id:0,x:.2,y:.45,w:.6,h:.1}]
  const fitted=fitBoxes(boxes,1000,1000,1000,500)
  const visible=viewSize(1000,1000,1000,500,fitted.zoom)
  expect(fitted.zoom).toBeCloseTo(1000/(600*1.04)/.5)
  expect(visible.width).toBeCloseTo(624)
  expect(visible.height).toBeCloseTo(312)
  expect(fitted.view.x).toBeCloseTo(188)
 })
 it('sorts by tile position without moving boxes',()=>{const boxes=[{class_id:0,x:.8,y:.2,w:.1,h:.1},{class_id:1,x:.1,y:.7,w:.1,h:.1}];const original=JSON.stringify(boxes);expect(ordered(boxes,'horizontal')).toEqual([1,0]);expect(ordered(boxes,'vertical')).toEqual([0,1]);expect(JSON.stringify(boxes)).toBe(original)})
 it('places each red five after its regular five without changing class IDs',()=>{
  const groups=groupedClasses(['5mr','6m','5m','0p','6p','5p','5sr','6s','5s','east'])
  expect(groups['萬子']).toEqual([{name:'5m',id:2},{name:'5mr',id:0},{name:'6m',id:1}])
  expect(groups['筒子']).toEqual([{name:'5p',id:5},{name:'0p',id:3},{name:'6p',id:4}])
  expect(groups['索子']).toEqual([{name:'5s',id:8},{name:'5sr',id:6},{name:'6s',id:7}])
  expect(groups['赤牌']).toBeUndefined()
  expect(category('east')).toBe('風牌')
  expect(tileName('0p')).toBe('赤5筒')
 })
 it('constrains coordinate edits',()=>{expect(clamp(1.2,0,1)).toBe(1)})
})
