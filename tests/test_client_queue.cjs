// Behavioral tests; synthetic IndexedDB and fetch only, no real network or browser.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const root=path.resolve(process.env.SCRIBELET_APP_ROOT||path.join(__dirname,'..')),{IDBFactory}=require(path.join(root,'node_modules/fake-indexeddb'));
function context({pending=false}={}){
 const nodes=new Map(),events={},submitted=[];let release,entered;
 const held=new Promise(r=>release=r),started=new Promise(r=>entered=r);
 const node=id=>{if(!nodes.has(id))nodes.set(id,{value:'',hidden:false,textContent:'',innerHTML:'',checked:false,disabled:false,classList:{add(){},toggle(){}},querySelector:()=>({disabled:false})});return nodes.get(id);};
 const response=data=>({ok:true,status:200,json:async()=>data});
 const db=new IDBFactory();
 const c=vm.createContext({Blob,crypto:require('node:crypto').webcrypto,structuredClone,indexedDB:db,AbortController,URL,Date,console,
 document:{getElementById:node,addEventListener(){},hidden:false},window:{addEventListener:(name,f)=>events[name]=f},navigator:{onLine:true},confirm:()=>true,
 setInterval:()=>0,clearInterval(){},setTimeout:(f,n)=>{const t=setTimeout(f,n);t.unref();return t;},clearTimeout,
 fetch:async(url,o)=>{
  if(url==='/api/me')return response({csrf:'csrf',key_ready:false});
  if(url==='/api/records'&&o.method==='GET')return response([]);
  if(url==='/api/records'){
   const id=JSON.parse(o.body).id;
   if(id==='a'.repeat(32)){entered();await held;}
   return response({id});
  }
  if(url.endsWith('/submit')){const id=url.split('/')[3];if(process.env.REQUIRE_SUBMISSION_RECEIPT!=='0')assert.equal(JSON.parse(o.body).submission_id,id,'automatic submission must reuse its stable receipt key');submitted.push(id);return response({ok:true});}
  return response({state:'uploading',parts:[]});
 }});
 const run=code=>vm.runInContext(code,c);
 const seed=id=>run(`dbWrite('recordings',{id:'${id}',title:'synthetic',mime:'audio/wav',size:1,count:1,created:1,complete:true,autoUpload:true}).then(()=>dbWrite('chunks',{id:'${id}:0',blob:new Blob(['x'])}))`);
 return {c,run,seed,events,submitted,release,started,db};
}
async function busy(){
 const c=context();vm.runInContext(fs.readFileSync(path.join(root,'static/app.js'),'utf8'),c.c);
 await c.run('database');await c.seed('a'.repeat(32));await c.seed('b'.repeat(32));
 const first=c.run(`upload('${'a'.repeat(32)}')`);await c.started;
 await c.run(`upload('${'b'.repeat(32)}')`);c.release();await first;
 await new Promise(r=>setTimeout(r,50));
 assert.deepEqual(c.submitted,['a'.repeat(32),'b'.repeat(32)]);
 console.log('PASS busy: second recording drains after current upload');
}
async function boot(){
 const c=context();
 // Prepare the same DB before app boot, as if a previous page closed offline.
 await new Promise((resolve,reject)=>{const r=c.db.open('scribelet',1);r.onupgradeneeded=()=>{for(const n of ['recordings','chunks','drafts'])r.result.createObjectStore(n,{keyPath:'id'});};r.onsuccess=()=>{const d=r.result,t=d.transaction(['recordings','chunks'],'readwrite'),id='b'.repeat(32);t.objectStore('recordings').put({id,title:'synthetic',mime:'audio/wav',size:1,count:1,created:1,complete:true,autoUpload:true});t.objectStore('chunks').put({id:id+':0',blob:new Blob(['x'])});t.oncomplete=()=>{d.close();resolve();};};r.onerror=()=>reject(r.error);});
 vm.runInContext(fs.readFileSync(path.join(root,'static/app.js'),'utf8'),c.c);
 await new Promise(r=>setTimeout(r,100));
 assert.deepEqual(c.submitted,['b'.repeat(32)]);
 console.log('PASS boot: persisted pending recording submits without an online event');
}
(async()=>{if(process.argv[2]==='boot')await boot();else await busy();})().catch(e=>{console.error(e);process.exitCode=1;});
