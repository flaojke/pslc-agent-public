import {useId,useState} from 'react';

/** Presentation only. The full source remains the same bounded temporary conversation text. */
export function MessageText({text,latest,author}:{text:string;latest:boolean;author:string}){
 const [expanded,setExpanded]=useState(false);
 const contentId=useId();
 const long=text.length>600||text.split(/\r?\n/).length>8;
 const collapsed=long&&!latest&&!expanded;
 const first=text.trimStart().split(/\r?\n/,1)[0].trimEnd();
 return <div className={'message-text'+(collapsed?' is-collapsed':'')}>
  <p id={contentId}>{collapsed?first+'…':text}</p>
  {long&&!latest&&<button type="button" className="ghost message-toggle" aria-controls={contentId} aria-expanded={!collapsed} aria-label={(collapsed?'Развернуть':'Свернуть')+' сообщение '+author} onClick={()=>setExpanded(!expanded)}>{collapsed?'Развернуть':'Свернуть'}</button>}
 </div>;
}
