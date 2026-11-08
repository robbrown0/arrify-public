const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),{MessageChannel}=require('node:worker_threads');
test('read-only worker sample reports playable seconds separately and avoids stale-track attribution',async()=>{
 let calls=0;const panel={innerHTML:''};const fixture={state:'active',downloaded_bytes:10,limit_bytes:100,items:[{id:7,state:'downloading',loaded_bytes:10,total_bytes:20}]};
 const ctx={window:{},document:{querySelector:s=>s==='#sound-dialog'?{open:true}:panel},navigator:{serviceWorker:{controller:{postMessage:(msg,ports)=>{calls++;assert.equal(msg.type,'device-buffer-status');ports[0].postMessage(fixture)}}}},MessageChannel,setTimeout,clearTimeout,Date,Math};vm.runInNewContext(fs.readFileSync('web/buffer-status.js','utf8'),ctx);
 const module=ctx.window.ArrifyBufferStatus,audio={currentTime:12,buffered:{length:1,start:()=>0,end:()=>30}};assert.equal(module.report(audio,7),null);module.refresh(audio,7);module.refresh(audio,7);await new Promise(r=>setTimeout(r,30));assert.equal(calls,1);assert.equal(module.report(audio,7).player_seconds,18);assert.equal(module.report(audio,8),null);assert.match(panel.innerHTML,/50%/);assert.match(panel.innerHTML,/Player buffer: 18 seconds/);
});
test('failed read-ahead has an explicit retry reason without rendering arbitrary text',()=>{
 const ctx={window:{}};vm.runInNewContext(fs.readFileSync('web/buffer-status.js','utf8'),ctx);
 const report={state:'active',items:[{state:'retrying',failure_reason:'unsupported-response',loaded_bytes:0,total_bytes:0}]};
 assert.match(ctx.window.ArrifyBufferStatus.markup(report),/Waiting to retry · Audio response cannot be cached/);
 report.items[0].failure_reason='<script>bad</script>';
 assert.doesNotMatch(ctx.window.ArrifyBufferStatus.markup(report),/<script>/);
});
