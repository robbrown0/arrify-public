const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),{webcrypto}=require('node:crypto');
const app=fs.readFileSync('web/app.js','utf8');
const helper=app.slice(app.indexOf('async function setAccountBufferScope('),app.indexOf('async function refreshAppView('));
function fixture(crypto=webcrypto){const c={crypto,TextEncoder,Uint8Array};vm.createContext(c);vm.runInContext(helper,c);return c.setAccountBufferScope}
test('account refresh produces same cache scope; account or session change gets a different scope',async()=>{
 const scope=fixture(),a={id:2,csrf:'session-a'},refresh={...a},session={id:2,csrf:'session-b'},user={id:3,csrf:'session-a'};
 for(const account of [a,refresh,session,user])await scope(account);
 assert.match(a.buffer_scope,/^[a-f0-9]{64}$/);assert.equal(a.buffer_scope,refresh.buffer_scope);assert.notEqual(a.buffer_scope,session.buffer_scope);assert.notEqual(a.buffer_scope,user.buffer_scope);
});
test('unavailable secure hashing fails closed instead of keeping an old account scope',async()=>{
 const account={id:2,csrf:'new-session',buffer_scope:'old-scope'};await fixture({})(account);assert.equal(account.buffer_scope,'');
});
