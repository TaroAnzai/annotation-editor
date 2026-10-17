export type Box={class_id:number;x:number;y:number;w:number;h:number;bbox_confidence?:number;class_confidence?:number}
export const clamp=(v:number,min:number,max:number)=>Math.max(min,Math.min(max,v))
export function viewSize(width:number,height:number,stageWidth:number,stageHeight:number,zoom:number){
 if(stageWidth<=0||stageHeight<=0)return {width:width/zoom,height:height/zoom}
 const fitScale=Math.min(stageWidth/width,stageHeight/height)
 return {width:stageWidth/(fitScale*zoom),height:stageHeight/(fitScale*zoom)}
}
export function constrainView(x:number,y:number,width:number,height:number,visibleWidth:number,visibleHeight:number){
 return {x:visibleWidth>=width?(width-visibleWidth)/2:clamp(x,0,width-visibleWidth),y:visibleHeight>=height?(height-visibleHeight)/2:clamp(y,0,height-visibleHeight)}
}
export function fitBoxes(boxes:Box[],width:number,height:number,stageWidth=width,stageHeight=height){
 if(width<=0||height<=0||stageWidth<=0||stageHeight<=0)return {zoom:1,view:{x:0,y:0}}
 if(!boxes.length){const visible=viewSize(width,height,stageWidth,stageHeight,1);return {zoom:1,view:constrainView((width-visible.width)/2,(height-visible.height)/2,width,height,visible.width,visible.height)}}
 const bounds=boxes.reduce((b,box)=>({left:Math.min(b.left,box.x),top:Math.min(b.top,box.y),right:Math.max(b.right,box.x+box.w),bottom:Math.max(b.bottom,box.y+box.h)}),{left:1,top:1,right:0,bottom:0})
 const fitScale=Math.min(stageWidth/width,stageHeight/height)
 const boxWidth=(bounds.right-bounds.left)*width,boxHeight=(bounds.bottom-bounds.top)*height
 const zoom=Math.max(1,Math.min(stageWidth/(boxWidth*1.04),stageHeight/(boxHeight*1.04))/fitScale)
 const visible=viewSize(width,height,stageWidth,stageHeight,zoom)
 const view=constrainView((bounds.left+bounds.right)*width/2-visible.width/2,(bounds.top+bounds.bottom)*height/2-visible.height/2,width,height,visible.width,visible.height)
 return {zoom,view}
}
export function placeBoxLabel(boxes:Box[],index:number,imageWidth:number,imageHeight:number,labelWidth:number,labelHeight:number,viewport={x:0,y:0,width:imageWidth,height:imageHeight}){
 const box=boxes[index],left=box.x*imageWidth,top=box.y*imageHeight,right=(box.x+box.w)*imageWidth,bottom=(box.y+box.h)*imageHeight
 const candidates=[
  {side:'top',x:left,y:Math.max(0,top-labelHeight)},
  {side:'right',x:right,y:top},
  {side:'left',x:left-labelWidth,y:top},
  {side:'bottom',x:left,y:bottom},
 ]
 const viewLeft=Math.max(0,viewport.x),viewTop=Math.max(0,viewport.y)
 const viewRight=Math.min(imageWidth,viewport.x+viewport.width),viewBottom=Math.min(imageHeight,viewport.y+viewport.height)
 const fits=(candidate:typeof candidates[number])=>candidate.x>=viewLeft&&candidate.y>=viewTop&&candidate.x+labelWidth<=viewRight&&candidate.y+labelHeight<=viewBottom
 const tolerance=Math.min(labelWidth,labelHeight)*0.1
 const clear=(candidate:typeof candidates[number])=>boxes.every(other=>candidate.x+tolerance>=other.x*imageWidth+other.w*imageWidth||candidate.x+labelWidth-tolerance<=other.x*imageWidth||candidate.y+tolerance>=other.y*imageHeight+other.h*imageHeight||candidate.y+labelHeight-tolerance<=other.y*imageHeight)
 const chosen=candidates.find(candidate=>fits(candidate)&&clear(candidate))||candidates.find(clear)||candidates[0]
 return {...chosen,width:labelWidth,height:labelHeight}
}
export const ordered=(boxes:Box[],mode:'horizontal'|'vertical')=>boxes.map((_,i)=>i).sort((a,b)=>mode==='horizontal' ? (boxes[a].x+boxes[a].w/2)-(boxes[b].x+boxes[b].w/2) || boxes[a].y-boxes[b].y : (boxes[a].y+boxes[a].h/2)-(boxes[b].y+boxes[b].h/2) || boxes[a].x-boxes[b].x)
export function category(name:string){if(/^([1-9]m|red5m|5mr|0m)$/i.test(name))return '萬子';if(/^([1-9]p|red5p|5pr|0p)$/i.test(name))return '筒子';if(/^([1-9]s|red5s|5sr|0s)$/i.test(name))return '索子';if(/^(east|south|west|north)$/i.test(name))return '風牌';if(/^(white|green|red)$/i.test(name))return '三元牌';return 'その他'}
export function groupedClasses(classes:string[]){
 const groups:Record<string,{name:string;id:number}[]>={}
 classes.forEach((name,id)=>{const group=category(name);(groups[group]??=[]).push({name,id})})
 for(const group of ['萬子','筒子','索子'])groups[group]?.sort((a,b)=>tileRank(a.name)-tileRank(b.name)||a.id-b.id)
 return groups
}
function tileRank(name:string){return /^(red5.|5.r|0.)$/i.test(name)?11:Number(name[0])*2}
export function tileName(name:string){return ({east:'東',south:'南',west:'西',north:'北',white:'白',green:'發',red:'中','red5m':'赤5萬','red5p':'赤5筒','red5s':'赤5索','5mr':'赤5萬','5pr':'赤5筒','5sr':'赤5索','0m':'赤5萬','0p':'赤5筒','0s':'赤5索'} as Record<string,string>)[name.toLowerCase()]||name}
