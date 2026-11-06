// Unit tests, not browser automation: real IndexedDB semantics with synthetic data.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const {indexedDB}=require('fake-indexeddb');
const path=require('node:path');
const nodes=new Map();
function node(id){if(!nodes.has(id))nodes.set(id,{value:'',hidden:false,textContent:'',innerHTML:'',checked:false,disabled:false,addEventListener(){},querySelector:()=>({disabled:false})});return nodes.get(id);}
let failSecond=true,submitted=0;const puts=[],parts=new Map(),timeoutValues=[];
const response=(data,status=200)=>({ok:status===200,status,json:async()=>data});
const context=vm.createContext({Blob,crypto:require('node:crypto').webcrypto,structuredClone,indexedDB,console,AbortController,URL,Date,
  document:{getElementById:node,addEventListener(){},hidden:false,createElement:()=>({click(){}})},
  window:{addEventListener(){}},navigator:{onLine:true},confirm:()=>true,
  setInterval:()=>0,clearInterval(){},setTimeout:(fn,n)=>{timeoutValues.push(n);const t=setTimeout(fn,n);t.unref();return t;},clearTimeout,
  fetch:async(url,opts)=>{
    if(url==='/api/me')return response({csrf:'csrf',owner:'owner',key_ready:false});
    if(url==='/api/records'&&opts.method==='GET')return response([]);
    if(url==='/api/records')return response({id:JSON.parse(opts.body).id});
    if(url.includes('/parts/')){const idx=Number(url.split('/').pop());puts.push(idx);if(idx===1&&failSecond){failSecond=false;throw new Error('simulated disconnect');}parts.set(idx,opts.body);return response({ok:true});}
    if(url.endsWith('/submit')){submitted++;return response({ok:true,state:'queued'});}
    return response({parts:[...parts.keys()],state:'uploading'});
  }});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8'),context);
const run=code=>vm.runInContext(code,context);
(async()=>{
  await run('database');
  await run(`dbWrite('recordings',{id:'${'a'.repeat(32)}',title:'Synthetic',size:0,count:0,mime:'audio/wav',complete:true,created:1})`);
  context.synthetic=new Blob([Buffer.alloc(2*1024*1024,1),Buffer.from('tail')]);
  await run(`appendChunk('${'a'.repeat(32)}',synthetic)`);
  const before=await run(`dbRead('recordings','${'a'.repeat(32)}')`);
  assert.equal(before.size,2*1024*1024+4);
  await run(`upload('${'a'.repeat(32)}')`);
  assert.equal(submitted,0);assert.deepEqual(puts,[0,1]);
  assert.equal((await run(`dbRead('recordings','${'a'.repeat(32)}')`)).autoUpload,true);
  await run(`upload('${'a'.repeat(32)}')`);
  assert.deepEqual(puts,[0,1,1]);assert.equal(submitted,1);
  assert.ok(timeoutValues.includes(300000),'audio part PUT must allow five minutes on slow networks');
  const after=await run(`dbRead('recordings','${'a'.repeat(32)}')`);
  assert.equal(after.uploaded,true);assert.equal(after.autoUpload,false);
  assert.equal(await (await run('recordingBlob('+JSON.stringify(after)+')')).text(),await context.synthetic.text());
  await run("dbWrite('recordings',{id:'full',size:MAX,count:0})");
  await assert.rejects(run("appendChunk('full',new Blob(['x']))"));
  assert.equal((await run("dbRead('recordings','full')")).count,0);
  assert.equal(await run("dbRead('chunks','full:0')"),undefined);
  const html=run("markdown('# Test\n\n<script>alert(1)</script>\n\n| A | B |\n| --- | --- |\n| English<br>中文 | <img src=x onerror=alert(1)> |')".replace(/\n/g,'\\n'));
  assert.ok(!html.includes('<script>'));assert.ok(!html.includes('<img '));assert.ok(html.includes('English<br>中文'));
  console.log('PASS: IndexedDB persistence, resumed upload skips acknowledged parts, single submit, local backup retained, quota failure atomic, Markdown XSS escaped.');
})().catch(e=>{console.error(e);process.exitCode=1;});
