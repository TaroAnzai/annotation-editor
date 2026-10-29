import {useEffect,useRef} from 'react'

export default function ChoiceDropdown({label,value,options,onChange}:{label:string;value:string;options:{value:string;label:string}[];onChange:(value:string)=>void}){
 const menuRef=useRef<HTMLDetailsElement>(null)
 useEffect(()=>{const closeOutside=(event:PointerEvent)=>{if(menuRef.current&&!menuRef.current.contains(event.target as Node))menuRef.current.open=false};const closeEscape=(event:KeyboardEvent)=>{if(event.key==='Escape'&&menuRef.current?.open){menuRef.current.open=false;menuRef.current.querySelector('summary')?.focus()}};document.addEventListener('pointerdown',closeOutside);document.addEventListener('keydown',closeEscape);return()=>{document.removeEventListener('pointerdown',closeOutside);document.removeEventListener('keydown',closeEscape)}},[])
 return <div className="choice-dropdown-field"><span>{label}</span><details className="choice-dropdown" ref={menuRef}><summary aria-label={`${label}を選択`}>{options.find(option=>option.value===value)?.label||value}</summary><div className="choice-menu">{options.map(option=><button type="button" aria-pressed={value===option.value} key={option.value} onClick={()=>{onChange(option.value);if(menuRef.current)menuRef.current.open=false}}>{option.label}</button>)}</div></details></div>
}
