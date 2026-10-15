import {describe,it,expect} from 'vitest'
import {ordered,category,tileName,clamp} from './editor'
describe('editor utilities',()=>{
 it('sorts by tile position without moving boxes',()=>{const boxes=[{class_id:0,x:.8,y:.2,w:.1,h:.1},{class_id:1,x:.1,y:.7,w:.1,h:.1}];const original=JSON.stringify(boxes);expect(ordered(boxes,'horizontal')).toEqual([1,0]);expect(ordered(boxes,'vertical')).toEqual([0,1]);expect(JSON.stringify(boxes)).toBe(original)})
 it('derives class palette from names',()=>{expect(category('5mr')).toBe('赤牌');expect(category('east')).toBe('風牌');expect(tileName('white')).toBe('白')})
 it('constrains coordinate edits',()=>{expect(clamp(1.2,0,1)).toBe(1)})
})
