'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('web/app.js','utf8'),start=source.indexOf('function partyControlState(){'),end=source.indexOf('\nasync function applyState',start),fn=source.slice(start,end);
function fixture(p,state){const ctx={state,window:{ArrifyParty:{controlParty:()=>p}}};vm.createContext(ctx);vm.runInContext(fn,ctx);return JSON.parse(JSON.stringify(ctx.partyControlState()))}
const outside={current:99,track:{id:99,title:'Outside music'},upcoming:[100],history:[98]},party={id:'party',source:'arrify',name:'Party',items:[{id:1,title:'First party song',status:'queued'},{id:2,title:'Second party song',status:'queued'}]};
test('Native party preview cannot inherit unrelated current or history',()=>{const s=fixture(party,outside);assert.equal(s.current,1);assert.equal(s.party_preview,true);assert.equal(s.track.title,'First party song');assert.deepEqual(s.history,[]);assert.deepEqual(s.upcoming,[2])});
test('Empty native party has no track even while unrelated music is loaded',()=>{const s=fixture({...party,items:[]},outside);assert.equal(s.current,null);assert.equal(s.track,null);assert.deepEqual(s.upcoming,[])});
test('Native party uses actual playback once the listening session is bound',()=>{const playing={...outside,party_id:'party',current:2,track:{id:2,title:'Current party song'},position:42};const s=fixture(party,playing);assert.equal(s.current,2);assert.equal(s.position,42);assert.equal(s.party_preview,false)});
test('Non-party pages retain their normal listening state',()=>{assert.deepEqual(fixture(null,outside),outside)});
