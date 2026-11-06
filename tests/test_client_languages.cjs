// Lecture/note language choice: remembered per device, stored with each recording, sent on upload.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const {indexedDB}=require('fake-indexeddb');
const nodes=new Map();
const options=values=>values.map(value=>({value}));
function node(id){
  if(!nodes.has(id)){
    const n={value:'',hidden:false,textContent:'',innerHTML:'',checked:false,disabled:false,addEventListener(){},querySelector:()=>({disabled:false})};
    if(id==='sourceLang'){n.options=options(['auto','en','zh','es','ja']);n.value='en';}
    if(id==='targetLang'){n.options=options(['en','zh','es','ja']);n.value='zh';}
    nodes.set(id,n);
  }
  return nodes.get(id);
}
let stored=JSON.stringify({source:'es',target:'ja'});const created=[];
const response=(data,status=200)=>({ok:status===200,status,json:async()=>data});
const context=vm.createContext({Blob,crypto:require('node:crypto').webcrypto,structuredClone,indexedDB,console,AbortController,URL,Date,
  localStorage:{getItem:()=>stored,setItem:(_k,v)=>{stored=v;}},
  document:{getElementById:node,addEventListener(){},hidden:false,createElement:()=>({click(){}})},
  window:{addEventListener(){}},navigator:{onLine:true},confirm:()=>true,
  setInterval:()=>0,clearInterval(){},setTimeout:(fn,n)=>{const t=setTimeout(fn,n);t.unref();return t;},clearTimeout,
  fetch:async(url,opts)=>{
    if(url==='/api/me')return response({csrf:'csrf',owner:'owner',key_ready:true});
    if(url==='/api/records'&&opts.method==='GET')return response([]);
    if(url==='/api/records'){created.push(JSON.parse(opts.body));return response({id:JSON.parse(opts.body).id});}
    if(url.includes('/parts/'))return response({ok:true});
    if(url.endsWith('/submit'))return response({ok:true,state:'queued'});
    return response({parts:[],state:'uploading'});
  }});
vm.runInContext(fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8'),context);
const run=code=>vm.runInContext(code,context);
(async()=>{
  // The saved choice is restored on load.
  assert.equal(node('sourceLang').value,'es');assert.equal(node('targetLang').value,'ja');
  // Changing a box remembers the pair for next time.
  node('targetLang').value='en';node('targetLang').onchange();
  assert.deepEqual(JSON.parse(stored),{source:'es',target:'en'});
  // A saved value that is no longer offered is ignored instead of selecting nothing.
  stored=JSON.stringify({source:'xx',target:'zh'});node('sourceLang').value='en';run('restoreLanguages()');
  assert.equal(node('sourceLang').value,'en');assert.equal(node('targetLang').value,'zh');

  await run('database');
  const withLangs='b'.repeat(32),legacy='c'.repeat(32);
  await run(`dbWrite('recordings',{id:'${withLangs}',title:'Clase',size:0,count:0,mime:'audio/wav',complete:true,created:1,source_lang:'es',target_lang:'en'})`);
  await run(`dbWrite('recordings',{id:'${legacy}',title:'Old',size:0,count:0,mime:'audio/wav',complete:true,created:2})`);
  for(const id of [withLangs,legacy])await run(`appendChunk('${id}',new Blob(['audio']))`);
  await run(`upload('${withLangs}')`);await run(`upload('${legacy}')`);
  const first=created.find(b=>b.id===withLangs),second=created.find(b=>b.id===legacy);
  assert.equal(first.source_lang,'es');assert.equal(first.target_lang,'en');
  // Recordings saved before language choice existed send nothing and get the server default.
  assert.ok(!('source_lang' in second)&&!('target_lang' in second));
  console.log('PASS: languages restore and persist; each recording uploads with its own pair; legacy recordings use the server default.');
})().catch(error=>{console.error(error);process.exit(1);});
