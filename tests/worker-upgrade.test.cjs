const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
function setup(addAll){
 const handlers={},imports=[];let skipped=0;
 const ctx={importScripts:u=>imports.push(u),ArrifyDeviceBuffer:class{},self:{addEventListener:(n,f)=>handlers[n]=f,skipWaiting:async()=>{skipped++}},caches:{open:async()=>({addAll})}};
 vm.runInNewContext(fs.readFileSync('web/sw.js','utf8'),ctx);
 return {imports,skipped:()=>skipped,install(){let work;handlers.install({waitUntil:p=>work=p});return work}};
}
test('cache repair activates only after new shell assets have installed',async()=>{
 let finish;const f=setup(()=>new Promise(r=>finish=r)),work=f.install();
 await new Promise(setImmediate);assert.equal(f.skipped(),0);finish();await work;
 assert.equal(f.skipped(),1);assert.match(f.imports[0],/release=20261108-file-recovery/);
});
test('failed installation does not replace the working controller',async()=>{
 const f=setup(async()=>{throw Error('offline')});await assert.rejects(f.install(),/offline/);assert.equal(f.skipped(),0);
});
