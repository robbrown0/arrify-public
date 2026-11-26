const test=require('node:test'),assert=require('node:assert/strict');
const {time,partAt,chapterAt,ProgressOutbox}=require('../web/audiobook-core.js');
test('long-form timeline retains hours and selects the right part at chapter boundaries',()=>{
 assert.equal(time(90061),'25:01:01');assert.equal(time(61),'1:01');
 const parts=[{index:0,start:0},{index:1,start:100},{index:2,start:200}];
 assert.equal(partAt(parts,100).index,1);assert.equal(partAt(parts,250).index,2);
 assert.equal(chapterAt([{title:'first',start:0},{title:'second',start:120}],120).title,'second');
});
test('a lost checkpoint acknowledgement cannot erase newer offline progress',()=>{
 const storage=new Map();storage.getItem=storage.get.bind(storage);storage.setItem=storage.set.bind(storage);storage.removeItem=storage.delete.bind(storage);
 const box=new ProgressOutbox(storage,'reader-one'),first={lease:'one',seq:1,position:100},rewind={lease:'one',seq:2,position:50};
 box.put(first);box.put(rewind);box.acknowledge(first);assert.deepEqual(box.read(),rewind);
 const relaunched=new ProgressOutbox(storage,'reader-one');assert.deepEqual(relaunched.read(),rewind);
 const other=new ProgressOutbox(storage,'reader-two');assert.equal(other.read(),null);
 box.acknowledge(rewind);assert.equal(box.read(),null);
});
