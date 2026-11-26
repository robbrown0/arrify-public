/* Shared long-form timeline helpers. No music queue or playback state is changed here. */
'use strict';
((root)=>{
 const time=seconds=>{const n=Math.max(0,Math.floor(Number(seconds)||0)),h=Math.floor(n/3600),m=Math.floor(n%3600/60),s=String(n%60).padStart(2,'0');return h?`${h}:${String(m).padStart(2,'0')}:${s}`:`${m}:${s}`};
 const partAt=(parts,position)=>{let result=parts[0];for(const part of parts){if(part.start>position)break;result=part}return result};
 const chapterAt=(chapters,position)=>{let result=chapters[0];for(const chapter of chapters){if(chapter.start>position)break;result=chapter}return result};
 class ProgressOutbox{
  constructor(storage,key){this.storage=storage;this.key=key}
  read(){try{const value=JSON.parse(this.storage.getItem(this.key));return value&&typeof value==='object'&&typeof value.lease==='string'&&Number.isSafeInteger(value.seq)?value:null}catch{return null}}
  put(value){this.storage.setItem(this.key,JSON.stringify(value))}
  acknowledge(value){const current=this.read();if(current?.lease===value.lease&&current.seq===value.seq)this.clear()}
  clear(){this.storage.removeItem(this.key)}
 }
 const api={time,partAt,chapterAt,ProgressOutbox};root.ArrifyAudiobookCore=api;if(typeof module!=='undefined')module.exports=api;
})(typeof window!=='undefined'?window:globalThis);
