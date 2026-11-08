const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('web/app.js','utf8');const context={};vm.runInNewContext(source.slice(source.indexOf('function bufferingHasRecovered('),source.indexOf('function bindAudio(')),context);
const media=()=>({currentTime:205,paused:false,seeking:false,ended:false,error:null,readyState:3});
test('advancing playback recovers buffering even without another playing event',()=>{assert.equal(context.bufferingHasRecovered(media(),204.75),true)});
test('stalled, paused, seeking, ended, errored and unready media do not report recovery',()=>{assert.equal(context.bufferingHasRecovered(media(),205),false);assert.equal(context.bufferingHasRecovered(media(),null),false);for(const change of [{paused:true},{seeking:true},{ended:true},{error:{}},{readyState:1}])assert.equal(context.bufferingHasRecovered({...media(),...change},204.75),false)});
test('seek jumps and backwards position changes do not prove playback resumed',()=>{assert.equal(context.bufferingHasRecovered(media(),30),false);assert.equal(context.bufferingHasRecovered(media(),206),false)});
