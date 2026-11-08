const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
test('Spotify heartbeat does not overlap or duplicate reports, and still observes state while hidden',async()=>{
 const text=fs.readFileSync(path.join(__dirname,'../web/spotify.js'),'utf8'),code=text.slice(text.indexOf(' let heartbeatRunning='),text.indexOf(' setInterval(paintClock',text.indexOf(' let heartbeatRunning=')));
 let release,reads=0,states=0,reports=0;const pending=new Promise(r=>release=r),ctx={active:true,setTimeout,clearTimeout,document:{hidden:true},refresh:()=>new Promise(()=>{}),player:{getCurrentState:()=>{reads++;return pending}},onState:async()=>{states++},report:async()=>reports++};vm.createContext(ctx);vm.runInContext(code,ctx);
 const first=ctx.heartbeat();await ctx.heartbeat();assert.equal(reads,1);release({paused:false});await first;assert.equal(states,1);assert.equal(reports,0);await ctx.heartbeat();assert.equal(reads,2);

});
test('host refresh shares an unfinished request and resumes after failure',async()=>{const text=fs.readFileSync(path.join(__dirname,'../web/party-host.js'),'utf8'),code=text.slice(text.indexOf(' let hostRefresh='),text.indexOf(' async function refreshOnce'));let calls=0,release;const ctx={refreshOnce:()=>{calls++;return new Promise(r=>release=r)}};vm.createContext(ctx);vm.runInContext(code,ctx);const first=ctx.refresh();assert.equal(ctx.refresh(),first);assert.equal(calls,1);release();await first;ctx.refresh();assert.equal(calls,2);release()});
