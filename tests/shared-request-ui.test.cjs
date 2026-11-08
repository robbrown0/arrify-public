const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const app=fs.readFileSync('web/app.js','utf8'),browse=fs.readFileSync('web/browse.js','utf8');
const ctx={esc:s=>String(s??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;'),browseRequestsInFlight:new Map(),me:{id:2}};vm.createContext(ctx);
vm.runInContext(app.slice(app.indexOf('function requestResult('),app.indexOf('function renderCatalogResults(')),ctx);
const catalog=ctx.requestResult({artist:'Artist',title:'Song',album:'Album',selection:'song',requestable:true,requested:true});assert.ok(catalog.includes('>Requested</button>'));assert.ok(catalog.includes('disabled'));
const start=browse.indexOf('function browseChartRows('),end=browse.indexOf('\n',start);vm.runInContext(browse.slice(start,end),ctx);
const chart=ctx.browseChartRows({genre:'pop',items:[{artist:'Artist',title:'Song',rank:1,request_key:'key',requested:true}]});assert.ok(chart.includes('>Requested</button>'));assert.ok(chart.includes('disabled'));console.log('Shared requested markers render disabled across chart and catalog results.');
