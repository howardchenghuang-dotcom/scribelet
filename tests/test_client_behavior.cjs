// Behavioral VM tests for capture constraints and source restoration.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const root=path.resolve(process.env.SCRIBELET_APP_ROOT||path.join(__dirname,'..'));
const {indexedDB}=require(path.join(root,'node_modules/fake-indexeddb'));
const source=fs.readFileSync(path.join(root,'static/app.js'),'utf8');

function makeContext({saved=null,sourceValue='screen',display=true,storageThrows=false,backupWarning=''}={}){
  const nodes=new Map(),calls=[];
  function node(id){
    if(!nodes.has(id))nodes.set(id,{value:id==='source'?sourceValue:'',hidden:id==='ipadSteps',textContent:'',innerHTML:'',
      checked:false,disabled:false,addEventListener(){},classList:{toggle(){},add(){}},querySelector:()=>({disabled:false})});
    return nodes.get(id);
  }
  const track={stop(){},onended:null};
  const media={getAudioTracks:()=>[track],getTracks:()=>[track]};
  const mediaDevices={
    getUserMedia:async options=>{calls.push(['user',options]);return media;},
  };
  if(display)mediaDevices.getDisplayMedia=async options=>{calls.push(['display',options]);return media;};
  class Recorder{
    static isTypeSupported(){return true;}
    constructor(_stream,options){this.mimeType=options.mimeType;this.state='inactive';}
    start(slice){this.state='recording';this.timeslice=slice;}
    stop(){this.state='inactive';}
  }
  class Stream{constructor(tracks){this.tracks=tracks;}getAudioTracks(){return this.tracks;}}
  const response=(data,status=200)=>({ok:status===200,status,json:async()=>data});
  const localStorage={
    getItem(){if(storageThrows)throw new Error('blocked');return saved;},
    setItem(_key,value){if(storageThrows)throw new Error('blocked');saved=value;}
  };
  const context=vm.createContext({Blob,crypto:require('node:crypto').webcrypto,structuredClone,indexedDB,console,
    AbortController,URL,Date,localStorage,MediaRecorder:Recorder,MediaStream:Stream,
    document:{getElementById:node,addEventListener(){},hidden:false,createElement:()=>({click(){}})},
    window:{addEventListener(){},MediaRecorder:Recorder},
    navigator:{onLine:true,mediaDevices,storage:{persist:async()=>true}},confirm:()=>true,
    setInterval:()=>0,clearInterval(){},setTimeout:(fn)=>{const t=setTimeout(fn,0);t.unref();return t;},clearTimeout,
    fetch:async url=>url==='/api/me'?response({csrf:'csrf',owner:'owner',key_ready:false,backup_warning:backupWarning}):response([])});
  vm.runInContext(source,context);
  return {context,node,calls,getSaved:()=>saved};
}

(async()=>{
  const mic=makeContext({sourceValue:'mic'});
  await vm.runInContext('startRecording()',mic.context);
  assert.equal(mic.calls.length,1);
  assert.equal(mic.calls[0][0],'user');
  assert.deepEqual(JSON.parse(JSON.stringify(mic.calls[0][1])),{audio:{
    echoCancellation:false,noiseSuppression:false,autoGainControl:false}});

  const remembered=makeContext({saved:'screen',sourceValue:'mic'});
  assert.equal(remembered.node('source').value,'screen');
  remembered.node('source').value='mic';remembered.node('source').onchange();
  assert.equal(remembered.getSaved(),'mic');

  const ipad=makeContext({sourceValue:'screen',display:false,storageThrows:true});
  assert.equal(ipad.node('source').value,'mic');
  assert.doesNotThrow(()=>ipad.node('source').onchange());
  const warning=makeContext({backupWarning:'异地备份测试告警'});
  await vm.runInContext('refreshSession()',warning.context);
  assert.match(warning.node('accountStatus').textContent,/异地备份测试告警/);
  console.log('PASS: getUserMedia receives all three disabled processing constraints; saved source restores; blocked storage is harmless; no-display device falls back to microphone.');
})().catch(error=>{console.error(error);process.exitCode=1;});

// ---- 低音量提示与 iPad 录屏指引 ----
{
  const {context,node}=makeContext({display:false});
  const src=node('source');
  assert.equal(src.value,'mic','没有共享能力时必须退回麦克风');
  assert.equal(node('ipadSteps').hidden,false,'iPad 四步指引必须显示');
  assert.equal(typeof context.quietStreak,'function');
  let streak=0;for(let i=0;i<14;i++)streak=context.quietStreak(0.4,streak);
  assert.equal(streak,14);
  assert.equal(context.quietStreak(0.4,streak),15,'第 15 秒达到告警阈值');
  assert.equal(context.quietStreak(3,streak),0,'一有声音就清零，不会误报');
  assert.match(source,/几乎没有声音/,'低电平提示文案必须存在');
}
{
  const {node}=makeContext({display:true});
  assert.equal(node('ipadSteps').hidden,true,'能共享标签页的设备不显示 iPad 指引');
}
console.log('PASS: no-display device shows iPad steps and disables tab sharing; quiet-streak detector counts and resets.');

// ---- 录完自动上传：只有 ≥1 分钟且没出错的录音才自动 ----
{
  const {context}=makeContext({display:true});
  assert.equal(typeof context.shouldAutoUpload,'function');
  assert.equal(context.shouldAutoUpload(60000,false),true,'满 1 分钟自动上传');
  assert.equal(context.shouldAutoUpload(59999,false),false,'不足 1 分钟保持手动');
  assert.equal(context.shouldAutoUpload(3600000,true),false,'录音出过错不自动上传');
  assert.match(source,/正在自动上传并生成笔记/);
}
console.log('PASS: auto-upload gate: >=60s and clean recordings only.');

