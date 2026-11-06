// Safari (Private Browsing, some WebKit builds) refuses Blobs in IndexedDB. Recordings must still be saved.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const fake=require('fake-indexeddb');
const {indexedDB,IDBObjectStore}=fake;
const put=IDBObjectStore.prototype.put;
let refuseBlobs=true;
IDBObjectStore.prototype.put=function(value,...rest){
  if(refuseBlobs&&value&&Object.values(value).some(v=>v instanceof Blob)){
    const error=new Error('Blobs are not supported here');error.name='DataCloneError';throw error;
  }
  return put.call(this,value,...rest);
};
const node=()=>({value:'',hidden:false,textContent:'',innerHTML:'',addEventListener(){},classList:{toggle(){},add(){}},querySelector:()=>({disabled:false})});
const nodes=new Map();
const context=vm.createContext({Blob,crypto:require('node:crypto').webcrypto,structuredClone,indexedDB,console,AbortController,URL,Date,
  document:{getElementById:id=>{if(!nodes.has(id))nodes.set(id,node());return nodes.get(id);},addEventListener(){},hidden:false,createElement:()=>({click(){}})},
  window:{addEventListener(){}},navigator:{onLine:true},confirm:()=>true,setInterval:()=>0,clearInterval(){},
  setTimeout:(fn,n)=>{const t=setTimeout(fn,n);t.unref();return t;},clearTimeout,fetch:async()=>({ok:true,status:200,json:async()=>[]})});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8'),context);
const run=code=>vm.runInContext(code,context);
// Rule: nothing in app.js may write a Blob into IndexedDB. Store bytes (arrayBuffer) and rebuild the Blob when reading.
{
  const source=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');
  const offenders=[...source.matchAll(/\.put\(\{[^}]*\bblob\b[^}]*\}\)/g)].map(m=>m[0]);
  assert.deepEqual(offenders,[],'Safari refuses Blobs in IndexedDB; store bytes instead: '+offenders.join(' | '));
}
(async()=>{
  await run('database');
  const id='e'.repeat(32);
  await run(`dbWrite('recordings',{id:'${id}',title:'Safari',size:0,count:0,mime:'audio/mp4',complete:false,created:1})`);
  context.first=new Blob(['first part '],{type:'audio/mp4'});context.second=new Blob(['second part'],{type:'audio/mp4'});
  await run(`appendChunk('${id}',first)`);await run(`appendChunk('${id}',second)`);
  const saved=await run(`dbRead('recordings','${id}')`);
  assert.equal(saved.count,2);assert.equal(saved.size,22);
  const audio=await run(`(async()=>recordingBlob(await dbRead('recordings','${id}')))()`);
  assert.equal(await audio.text(),'first part second part');assert.equal(audio.type,'audio/mp4');

  // Recordings saved by an earlier version kept Blobs; they must still play back.
  refuseBlobs=false;
  const old='f'.repeat(32);
  await run(`dbWrite('recordings',{id:'${old}',title:'Old',size:3,count:1,mime:'audio/webm',complete:true,created:2})`);
  await run(`dbWrite('chunks',{id:'${old}:0',blob:new Blob(['old'])})`);
  const legacy=await run(`(async()=>recordingBlob(await dbRead('recordings','${old}')))()`);
  assert.equal(await legacy.text(),'old');
  console.log('PASS: recordings save where Blobs are refused (Safari), and recordings saved by earlier versions still load.');
})().catch(error=>{console.error(error);process.exit(1);});
