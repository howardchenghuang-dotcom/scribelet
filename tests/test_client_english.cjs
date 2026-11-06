// English interface: every Chinese string a person can see has an English version, and the switch works.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const {indexedDB}=require('fake-indexeddb');
const root=path.join(__dirname,'..');
const source=fs.readFileSync(path.join(root,'static/app.js'),'utf8');
const CJK=/[一-鿿]/;

function load({saved=null,browser}={}){
  const nodes=new Map();
  const node=id=>{if(!nodes.has(id))nodes.set(id,{value:'',hidden:false,textContent:'',innerHTML:'',checked:false,disabled:false,
    addEventListener(){},classList:{toggle(){},add(){}},querySelector:()=>({disabled:false})});return nodes.get(id);};
  let stored=saved,reloaded=0;
  const context=vm.createContext({Blob,crypto:require('node:crypto').webcrypto,structuredClone,indexedDB,console,AbortController,URL,Date,
    localStorage:{getItem:()=>stored,setItem:(_k,v)=>{stored=v;}},location:{reload:()=>reloaded++},
    document:{getElementById:node,addEventListener(){},hidden:false,createElement:()=>({click(){}})},
    window:{addEventListener(){}},navigator:{onLine:true,language:browser},confirm:()=>true,
    setInterval:()=>0,clearInterval(){},setTimeout:(fn,n)=>{const t=setTimeout(fn,n);t.unref();return t;},clearTimeout,
    fetch:async url=>({ok:true,status:200,json:async()=>url.startsWith('/api/records/')?
      {id:'d'.repeat(32),title:'Lecture',state:'error',error:'模型服务暂时限流；录音已保存，可稍后重试。',note:'',transcript:'',version:1,size:1}:[]})});
  vm.runInContext(source,context);
  return {run:code=>vm.runInContext(code,context),node,stored:()=>stored,reloaded:()=>reloaded};
}

// Every string a person can see: wrapped literals, status labels, static page text, and server messages.
const keys=new Set();
for(const m of source.matchAll(/(?<![A-Za-z_.$])t\('((?:[^'\\\n]|\\.)*)'/g))keys.add(m[1].replace(/\\n/g,'\n').replace(/\\'/g,"'"));
for(const m of source.match(/const labels=\{([^}]*)\}/)[1].matchAll(/'([^']*)'/g))if(CJK.test(m[1]))keys.add(m[1]);
const html=fs.readFileSync(path.join(root,'static/index.html'),'utf8');
for(const m of html.matchAll(/>([^<>]+)</g)){const text=m[1].trim();if(CJK.test(text))keys.add(text);}
for(const m of html.matchAll(/(?:placeholder|aria-label)="([^"]*)"/g))if(CJK.test(m[1]))keys.add(m[1]);
keys.add(html.match(/<title>([^<]*)<\/title>/)[1]);
// Startup-only configuration errors and the language names themselves are intentionally not translated.
const exempt=[/^SCRIBELET_/,/^预览模式/,/^公网服务/,/^必须先配置/,/^用量预留值无效$/,/^\[No speech detected/,/^简体中文$/,/^日本語$/,/^；/];
for(const file of ['app.py','processing.py'])
  for(const m of fs.readFileSync(path.join(root,file),'utf8').matchAll(/'([^'\n]*[一-鿿][^'\n]*)'/g))
    if(!exempt.some(rule=>rule.test(m[1])))keys.add(m[1]);

// Rule: a Chinese string in app.js must go through t() (or message()/toast(), which call it).
// Only the dictionary itself and the labels table (translated where they are shown) may hold bare Chinese.
{
  const body=source.replace(/const EN=\{[\s\S]*?\n\};/,'').replace(/const labels=\{[^}]*\}/,'').replace(/^\s*\/\/.*$/gm,'');
  const bare=[...body.matchAll(/(.{0,12})'([^'\n]*[一-鿿][^'\n]*)'/g)]
    .filter(m=>!/(?:^|[^A-Za-z_.$])(?:t|message|toast)\($/.test(m[1])).map(m=>m[2])
    .filter(text=>text!=='中文');  // the switch names Chinese in Chinese, on purpose
  assert.deepEqual(bare,[],'wrap these in t(): '+bare.join(' | '));
  for(const sink of [/\$\('noteState'\)\.textContent=t\(/,/esc\(t\(labels\[r\.state\]\)\)/,/esc\(t\(r\.error\)\)/])
    assert.match(source,sink,'status labels and server errors must be translated where they are shown');
}
const en=load({saved:'en'}),zh=load({saved:'zh'});
const missing=[...keys].filter(key=>!exempt.some(rule=>rule.test(key))&&CJK.test(en.run('t')(key)));
assert.deepEqual(missing,[],'every visible Chinese string needs an English entry');
assert.ok(keys.size>150,'the scan must actually find the strings ('+keys.size+')');

// Placeholders are filled in both languages; Chinese is untouched in the Chinese interface.
assert.equal(en.run("t('正在上传 {0}% · 设备上的录音副本已保留',42)"),'Uploading 42% · the copy on this device is kept');
assert.equal(zh.run("t('正在上传 {0}% · 设备上的录音副本已保留',42)"),'正在上传 42% · 设备上的录音副本已保留');
// A server error built from two sentences is translated half by half.
assert.equal(en.run("t('模型服务暂时限流；录音已保存，可稍后重试。')"),'The model service is rate-limiting; the recording is saved; try again later.');
// Anything already shown goes through the translator: status messages and toasts.
en.run("message('请先结束录音或等待上传结束。')");
assert.equal(en.node('notice').textContent,'Stop recording or wait for the upload to finish first.');
en.run("toast('已复制')");assert.equal(en.node('toast').textContent,'Copied');

// The reader's status line shows the state and the server's error in English.
(async()=>{
  await en.run("openNote('"+'d'.repeat(32)+"')");
  assert.equal(en.node('noteState').textContent,'Saved · waiting to retry · The model service is rate-limiting; the recording is saved; try again later.');
  // The library row does the same.
  en.run("records=[{id:'"+'d'.repeat(32)+"',title:'Lecture',state:'error',error:'上传尚未完成',created:0,deleted:0,size:1,progress:0}];drawLibrary()");
  assert.match(en.node('cloudList').innerHTML,/Saved · waiting to retry/);assert.match(en.node('cloudList').innerHTML,/The upload has not finished/);
  assert.ok(!CJK.test(en.node('cloudList').innerHTML.replace(/Lecture/,'')),'no Chinese left in a library row');
})().catch(error=>{console.error(error);process.exit(1);});

// With no saved pair, notes default to the interface language.
assert.equal(en.node('targetLang').value,'en');assert.equal(zh.node('targetLang').value,'zh');

// The default follows the browser; Chinese browsers get Chinese.
assert.equal(load({browser:'en-US'}).run('UI'),'en');
assert.equal(load({browser:'zh-TW'}).run('UI'),'zh');
assert.equal(load({browser:'fr-FR',saved:'zh'}).run('UI'),'zh','a saved choice wins over the browser');

// The sidebar switch offers the other language and remembers it.
assert.equal(en.node('uiLang').textContent,'中文');assert.equal(zh.node('uiLang').textContent,'English');
zh.node('uiLang').onclick();assert.equal(zh.stored(),'en');assert.equal(zh.reloaded(),1);
console.log('PASS: '+keys.size+' visible strings have English; placeholders, split server messages, sinks, browser default and the switch work.');
