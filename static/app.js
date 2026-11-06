'use strict';
const $=id=>document.getElementById(id), MAX=256*1024*1024, PART=2*1024*1024;
// Call-style processing is built for a headset voice chat: noise suppression treats a
// lecturer across the room as noise, echo cancellation erases sound played by this
// device, and all three permanently alter the original audio this app archives.
// Default to lecture audio; remember the owner's last choice per device, and fall
// back to the microphone where tab sharing does not exist (iPad Safari).
function restoreSource(){
  const box=$('source');
  let saved=null;try{saved=localStorage.getItem('scribelet-source');}catch{}
  if(saved==='mic'||saved==='screen')box.value=saved;
  if(!navigator.mediaDevices?.getDisplayMedia){
    // No tab-audio capture here (iPad Safari): hide the option that cannot work
    // and show the screen-recording route instead of a one-line hint.
    const screen=box.querySelector('option[value="screen"]');if(screen)screen.disabled=true;
    box.value='mic';const steps=$('ipadSteps');if(steps)steps.hidden=false;
  }
  box.onchange=()=>{try{localStorage.setItem('scribelet-source',box.value);}catch{}};
}
// Lecture and note languages: chosen per recording, remembered per device as the default.
// Recordings saved before this existed upload without languages and get the server
// default (English lecture, Chinese notes).
function restoreLanguages(){
  let saved=null;try{saved=JSON.parse(localStorage.getItem('scribelet-languages')||'null');}catch{}
  for(const [id,key] of [['sourceLang','source'],['targetLang','target']]){
    const box=$(id);if(!box)continue;
    if(saved&&typeof saved[key]==='string'&&[...(box.options||[])].some(o=>o.value===saved[key]))box.value=saved[key];
    box.onchange=()=>{try{localStorage.setItem('scribelet-languages',JSON.stringify(chosenLanguages()));}catch{}};
  }
}
function chosenLanguages(){return {source:$('sourceLang')?.value||'en',target:$('targetLang')?.value||'zh'};}
// Below this byte-domain RMS (0..128 scale) for this long, the take is probably not
// usable: a lecturer across the room, or a tab shared without its audio.
const QUIET_RMS=1.5,QUIET_SECONDS=15;
const QUIET_MESSAGE='已经 '+QUIET_SECONDS+' 秒几乎没有声音。现场录音请靠近讲台或提高输入音量；网课请确认共享时勾选了声音。';
function quietStreak(rms,streak){return rms<QUIET_RMS?streak+1:0;}
// Recording is a means to notes: ship it as soon as it ends. Anything under a
// minute is almost always a mis-tap or a test, and each generation costs a
// daily quota slot, so those stay manual.
const AUTO_UPLOAD_MIN_MS=60000;
function shouldAutoUpload(durationMs,captureError){return !captureError&&durationMs>=AUTO_UPLOAD_MIN_MS;}
const MIC_CONSTRAINTS={echoCancellation:false,noiseSuppression:false,autoGainControl:false};
let csrf='', authenticated=false, recording=null, stream=null, recorder=null, writing=Promise.resolve(), uploading=false, records=[], current=null, editing=false, meterContext=null, wakeLock=null, captureError=false;
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const inline=s=>esc(s).replace(/&lt;br\s*\/?&gt;/gi,'<br>').replace(/`([^`]+)`/g,'<code>$1</code>').replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>');
function markdown(text){
  let html='',code=false,list=false,table=false;
  const close=()=>{if(list){html+='</ul>';list=false;}if(table){html+='</tbody></table>';table=false;}};
  const lines=String(text||'').split('\n');
  for(let i=0;i<lines.length;i++){
    const line=lines[i];
    if(/^```/.test(line)){close();html+=code?'</code></pre>':'<pre><code>';code=!code;continue;}
    if(code){html+=esc(line)+'\n';continue;}
    if(/^\|.*\|\s*$/.test(line)){if(/^\|[\s:|\-]+\|\s*$/.test(line))continue;if(!table){close();html+='<table><tbody>';table=true;}const tag=/^\|[\s:|\-]+\|\s*$/.test(lines[i+1]||'')?'th':'td';html+='<tr>'+line.trim().slice(1,-1).split('|').map(c=>`<${tag}>${inline(c)}</${tag}>`).join('')+'</tr>';continue;}
    if(/^\s*[-*+] /.test(line)){if(!list){close();html+='<ul>';list=true;}html+='<li>'+inline(line.replace(/^\s*[-*+] /,''))+'</li>';continue;}
    close();const heading=line.match(/^(#{1,6})\s+(.*)/);
    if(heading)html+=`<h${heading[1].length}>${inline(heading[2])}</h${heading[1].length}>`;
    else if(/^>/.test(line))html+='<blockquote>'+inline(line.slice(1))+'</blockquote>';
    else if(line.trim())html+='<p>'+inline(line)+'</p>';
  }
  close();if(code)html+='</code></pre>';return html;
}
function message(text){$('notice').textContent=text;$('notice').hidden=!text;}
function toast(text){$('toast').textContent=text;$('toast').hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('toast').hidden=true,5000);}
async function api(path,body,method,timeoutMs=60000){
  const controller=new AbortController(), timeout=setTimeout(()=>controller.abort(),timeoutMs);
  try{
    const response=await fetch('/api'+path,{method:method||(body===undefined?'GET':'POST'),credentials:'same-origin',cache:'no-store',signal:controller.signal,headers:body===undefined?{}:{'Content-Type':body instanceof Blob?'application/octet-stream':'application/json','X-CSRF-Token':csrf},body:body===undefined?undefined:body instanceof Blob?body:JSON.stringify(body)});
    const result=await response.json();
    if(!response.ok){if(response.status===401){authenticated=false;csrf='';$('loginPanel').hidden=false;if(!recording)$('workspace').hidden=true;}$('connection').textContent=navigator.onLine?'需要登录或重试':'离线 · 本地保存可用';throw new Error(result.detail||'操作失败');}
    return result;
  }catch(error){if(error.name==='AbortError')throw new Error('请求超时，设备上的录音仍在，可继续上传。');throw error;}finally{clearTimeout(timeout);}
}
const database=new Promise((resolve,reject)=>{
  const request=indexedDB.open('scribelet',1);
  request.onupgradeneeded=()=>{for(const name of ['recordings','chunks','drafts'])request.result.createObjectStore(name,{keyPath:'id'});};
  request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);
});
async function dbRead(store,id){const db=await database;return new Promise((resolve,reject)=>{const request=db.transaction(store).objectStore(store)[id===undefined?'getAll':'get'](id);request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);});}
async function dbWrite(store,value){const db=await database;return new Promise((resolve,reject)=>{const tx=db.transaction(store,'readwrite');tx.objectStore(store).put(value);tx.oncomplete=resolve;tx.onerror=()=>reject(tx.error);tx.onabort=()=>reject(tx.error||new Error('本地保存失败'));});}
async function appendChunk(id,blob){
  const record=await dbRead('recordings',id);
  if(record.size+blob.size>MAX)throw new Error('录音达到 256 MB 上限，请结束并开始下一段。');
  const db=await database;
  await new Promise((resolve,reject)=>{const tx=db.transaction(['recordings','chunks'],'readwrite');tx.objectStore('chunks').put({id:id+':'+record.count,blob});record.count++;record.size+=blob.size;tx.objectStore('recordings').put(record);tx.oncomplete=resolve;tx.onerror=()=>reject(tx.error);tx.onabort=()=>reject(tx.error||new Error('本地空间不足'));});
}
async function recordingBlob(record){const chunks=[];for(let i=0;i<record.count;i++){const chunk=await dbRead('chunks',record.id+':'+i);if(!chunk)throw new Error('本地录音分片缺失，请使用已下载的备份。');chunks.push(chunk.blob);}return new Blob(chunks,{type:record.mime});}
function uid(){return crypto.randomUUID().replace(/-/g,'');}
function title(){return $('title').value.trim()||'课堂记录 '+new Date().toLocaleString('zh-CN');}
function extension(mime){if(mime.includes('mp4'))return 'm4a';if(mime.includes('mpeg'))return 'mp3';if(mime.includes('wav'))return 'wav';return 'webm';}
function download(blob,name){const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),30000);}
async function localList(){
  const local=(await dbRead('recordings')).sort((a,b)=>b.created-a.created);
  $('localList').innerHTML=local.map(r=>`<div class="row"><div><strong>${esc(r.title)}</strong><small>${(r.size/1024/1024).toFixed(1)} MB · ${r.id===recording?'录音中':r.complete?'已保存到设备':'录音可能中断，已保留可恢复片段'}${r.uploaded?' · 已上传云端':''}</small></div><div class="actions"><button data-local-download="${r.id}" ${r.id===recording?'disabled':''}>下载备份</button><button data-upload="${r.id}" ${r.id===recording||uploading?'disabled':''}>${r.uploaded?'查看云端记录':'上传并生成'}</button></div></div>`).join('')||'<div class="empty">还没有本地录音。开始录音，或导入已有文件。</div>';
}
async function refreshSession(){const me=await api('/me');csrf=me.csrf;authenticated=true;$('loginPanel').hidden=true;$('workspace').hidden=false;$('logout').hidden=false;$('revoke').hidden=false;$('connection').textContent='已连接私人空间';$('accountStatus').textContent=me.key_ready?'硅基流动 · '+me.model+' · 模型密钥仅保存在服务器':'服务器尚未配置模型密钥，录音可以保存，生成将在配置后可用。';if(me.limits)$('accountStatus').textContent+=` 每日上限：${me.limits.audio_seconds/3600} 小时转写、${me.limits.jobs} 次生成尝试；超限暂停。`;if(me.backup_warning){$('accountStatus').textContent+=' ⚠ '+me.backup_warning;$('accountStatus').classList?.add('warning');}return me;}
restoreSource();restoreLanguages();
$('loginForm').onsubmit=async event=>{event.preventDefault();const button=event.submitter;button.disabled=true;try{await api('/login',{username:$('username').value,password:$('password').value});$('password').value='';await refreshSession();await loadLibrary();await resumeUploads();message('');}catch(e){message(e.message);}finally{button.disabled=false;}};
$('localOnly').onclick=()=>{$('loginPanel').hidden=true;$('workspace').hidden=false;show('captureView');message('本地录音模式。上传和生成时需要登录，云端不可用也可以先下载录音备份。');};
$('logout').onclick=async()=>{if(recording||uploading)return message('请先结束录音或等待上传结束。');try{await api('/logout',{});authenticated=false;csrf='';records=[];current=null;$('noteBody').textContent='';$('cloudList').textContent='';$('workspace').hidden=true;$('loginPanel').hidden=false;$('logout').hidden=$('revoke').hidden=true;message('已退出。此设备上的录音副本仍保留，请勿在共用设备上使用。');}catch(e){message(e.message);}};
$('revoke').onclick=async()=>{if(!confirm('让其他笔记本或 iPad 退出 Scribelet 登录？当前设备不受影响。\n注意：这只撤销 Scribelet 的登录，不影响 Cloudflare 的邮箱验证会话；对方设备下次进入仍需输入 Scribelet 密码。'))return;try{await api('/revoke-others',{});toast('其他设备已退出登录');}catch(e){message(e.message);}};

async function startRecording(){
  $('start').disabled=true;message('');
  try{
    if(!navigator.mediaDevices||!window.MediaRecorder)throw new Error('此浏览器不能录音，请使用系统录音后导入文件。');
    if(navigator.storage?.persist)await navigator.storage.persist();
    if($('source').value==='screen'){
      if(!navigator.mediaDevices.getDisplayMedia)throw new Error('此设备不支持网页共享声音，请使用系统录屏后导入。');
      stream=await navigator.mediaDevices.getDisplayMedia({video:true,audio:true});
      if(!stream.getAudioTracks().length){stream.getTracks().forEach(t=>t.stop());throw new Error('没有共享音频。请共享浏览器标签页并勾选声音；iPad 请改为导入录屏文件。');}
    }else stream=await navigator.mediaDevices.getUserMedia({audio:MIC_CONSTRAINTS});
    const audioStream=new MediaStream(stream.getAudioTracks());
    const mime=['audio/webm;codecs=opus','audio/mp4','audio/webm'].find(t=>MediaRecorder.isTypeSupported(t));
    recorder=new MediaRecorder(audioStream,mime?{mimeType:mime}:{});
    const id=uid();recording=id;writing=Promise.resolve();captureError=false;
    const langs=chosenLanguages();await dbWrite('recordings',{id,title:title(),mime:recorder.mimeType||mime||'audio/webm',created:Date.now(),size:0,count:0,complete:false,source_lang:langs.source,target_lang:langs.target});
    recorder.ondataavailable=event=>{if(!event.data.size)return;writing=writing.then(()=>appendChunk(id,event.data)).catch(error=>{captureError=true;message('录音保存出现问题：'+error.message+' 已保存片段可下载，请立即用系统录音接续。');if(recorder?.state==='recording')recorder.stop();});};
    recorder.onerror=()=>{captureError=true;message('录音被浏览器中断，已保存片段仍在。请下载检查后重新录制。');if(recorder?.state==='recording')recorder.stop();};
    recorder.onstop=async()=>{stream?.getTracks().forEach(t=>t.stop());await writing;const local=await dbRead('recordings',id);if(local){local.complete=!captureError;await dbWrite('recordings',local);}recording=null;$('start').hidden=false;$('stop').hidden=true;$('recordState').textContent=captureError?'需要检查录音':'已保存到设备';$('recordState').className='badge';clearInterval(startRecording.clock);await meterContext?.close();meterContext=null;await wakeLock?.release();wakeLock=null;await localList();if(shouldAutoUpload(Date.now()-started,captureError)){if(local){local.autoUpload=true;await dbWrite('recordings',local);}toast('录音已保存，正在自动上传并生成笔记。');await upload(id);}else toast(captureError?'录音可能不完整，请下载已保存片段检查。':'录音已保存在此设备。不足 1 分钟不会自动上传，需要的话点“上传并生成”。');};
    stream.getAudioTracks()[0].onended=()=>{if(recorder?.state==='recording'){message('音频来源已结束，正在保存录音。');recorder.stop();}};
    recorder.start(5000);$('start').hidden=true;$('stop').hidden=false;$('recordState').textContent='正在录音';$('recordState').className='badge recording';
    const started=Date.now();let last=started;
    try{wakeLock=await navigator.wakeLock?.request('screen');}catch{}
    try{meterContext=new AudioContext();const analyser=meterContext.createAnalyser();meterContext.createMediaStreamSource(audioStream).connect(analyser);startRecording.analyser=analyser;}catch{startRecording.analyser=null;}
    startRecording.clock=setInterval(()=>{const now=Date.now(),seconds=Math.floor((now-started)/1000);$('timer').textContent=Math.floor(seconds/60).toString().padStart(2,'0')+':'+(seconds%60).toString().padStart(2,'0');if(now-last>15000){captureError=true;message('页面曾被系统暂停，录音可能存在间断，请检查音频备份。');}last=now;if(startRecording.analyser){const data=new Uint8Array(startRecording.analyser.fftSize);startRecording.analyser.getByteTimeDomainData(data);const rms=Math.sqrt(data.reduce((s,n)=>s+(n-128)**2,0)/data.length);$('level').value=Math.min(100,rms*5);startRecording.quiet=quietStreak(rms,startRecording.quiet||0);if(startRecording.quiet===QUIET_SECONDS)message(QUIET_MESSAGE);}if(seconds>=14400&&recorder?.state==='recording')recorder.stop();},1000);
    await localList();
  }catch(e){stream?.getTracks().forEach(t=>t.stop());recording=null;message(e.message);}finally{$('start').disabled=false;}
}
$('start').onclick=startRecording;$('stop').onclick=()=>{if(recorder?.state==='recording'){recorder.stop();$('stop').disabled=true;writing.finally(()=>$('stop').disabled=false);}};
document.addEventListener('visibilitychange',()=>{if(document.hidden&&recording)message('页面进入后台，系统可能暂停录音。请返回此页面并检查声音。');});
window.addEventListener('beforeunload',event=>{if(recording||uploading){event.preventDefault();event.returnValue='';}});
$('file').onchange=async()=>{const file=$('file').files[0];if(!file)return;try{if(!file.size||file.size>MAX)throw new Error('文件必须大于 0 字节且小于 256 MB。');const id=uid(),langs=chosenLanguages();await dbWrite('recordings',{id,title:$('title').value.trim()||file.name,mime:file.type||'application/octet-stream',created:Date.now(),size:0,count:0,complete:false,source_lang:langs.source,target_lang:langs.target});for(let at=0;at<file.size;at+=PART)await appendChunk(id,file.slice(at,at+PART));const local=await dbRead('recordings',id);local.complete=true;local.autoUpload=true;await dbWrite('recordings',local);await localList();toast('文件已保存，正在自动上传并生成笔记。');await upload(id);}catch(e){message('导入失败：'+e.message);await localList();}finally{$('file').value='';}};
const uploadQueue=new Set();
async function resumeUploads(){for(const r of await dbRead('recordings'))if(r.autoUpload&&!r.uploaded)await upload(r.id);}
async function upload(id){
  if(uploading){uploadQueue.add(id);return;}uploading=true;await localList();
  try{
    if(!authenticated)await refreshSession();
    let local=await dbRead('recordings',id);
    if(local.uploaded){await openNote(id);return;}
    if(!local.complete&&!confirm('这段录音可能曾中断。仍上传已保存部分进行恢复？'))return;
    if(!local.size)throw new Error('没有已保存的音频。');
    local.autoUpload=true;await dbWrite('recordings',local);
    const blob=await recordingBlob(local);
    const body={id,title:local.title,size:blob.size,mime:local.mime};if(local.source_lang)body.source_lang=local.source_lang;if(local.target_lang)body.target_lang=local.target_lang;await api('/records',body);
    const server=await api('/records/'+id);
    if(server.state==='uploading')for(let i=0;i<Math.ceil(blob.size/PART);i++){
      if(server.parts.includes(i))continue;
      message('正在上传 '+Math.round(i*PART/blob.size*100)+'% · 设备上的录音副本已保留');
      await api('/records/'+id+'/parts/'+i,blob.slice(i*PART,(i+1)*PART),'PUT',300000);
    }
    await api('/records/'+id+'/submit',{submission_id:id});
    local.uploaded=true;local.autoUpload=false;await dbWrite('recordings',local);
    message('录音已上传，请在“我的笔记”查看生成状态；如需重试，请使用记录上的重试按钮。');await loadLibrary();show('libraryView');
  }catch(e){message(e.message+' 上传中断可继续，原音频仍在此设备。');}finally{uploading=false;await localList();const next=uploadQueue.values().next().value;if(next){uploadQueue.delete(next);await upload(next);}}
}
$('localList').onclick=async event=>{const button=event.target.closest('button');if(!button)return;try{if(button.dataset.upload)await upload(button.dataset.upload);else if(button.dataset.localDownload){const r=await dbRead('recordings',button.dataset.localDownload);download(await recordingBlob(r),r.title+'.'+extension(r.mime));}}catch(e){message(e.message);}};
function show(view){for(const id of ['captureView','libraryView','reader'])$(id).hidden=id!==view;$('homeNav').classList?.toggle('selected',view==='captureView');$('notesNav').classList?.toggle('selected',view!=='captureView');}
$('homeNav').onclick=()=>show('captureView');$('notesNav').onclick=()=>{show('libraryView');loadLibrary().catch(e=>message(e.message));};$('back').onclick=()=>show('libraryView');
const labels={uploading:'上传未完成',queued:'等待生成',processing:'正在转写和整理',done:'笔记已生成',error:'已保存 · 等待重试'};
function drawLibrary(){const query=$('search').value.trim().toLowerCase(),trash=$('showTrash').checked;$('cloudList').innerHTML=records.filter(r=>Boolean(r.deleted)===trash&&r.title.toLowerCase().includes(query)).map(r=>`<div class="row"><div><strong>${esc(r.title)}</strong><small>${new Date(r.created*1000).toLocaleString('zh-CN')} · ${esc(labels[r.state])}${r.state==='processing'?' '+r.progress+'%':''}</small>${r.error?'<p class="muted">'+esc(r.error)+'</p>':''}</div><div class="actions"><button data-open="${r.id}">阅读</button>${r.state==='done'&&r.size>0&&!r.deleted?`<button data-regenerate="${r.id}">重新生成笔记</button>`:''}${r.state==='error'&&!r.deleted?`<button data-retry="${r.id}">重试生成</button>`:''}<button data-trash="${r.id}" ${['queued','processing'].includes(r.state)?'disabled':''}>${r.deleted?'恢复':'删除'}</button>${r.deleted?`<button data-purge="${r.id}">彻底删除</button>`:''}</div></div>`).join('')||'<div class="empty">这里还没有记录。</div>';}
async function loadLibrary(){if(!authenticated)return;const next=await api('/records');if(JSON.stringify(next)!==JSON.stringify(records)){records=next;drawLibrary();}if($('notice').textContent.startsWith('录音已上传')&&records.length&&!records.some(r=>['queued','processing'].includes(r.state)))message('处理已结束，请在“我的笔记”查看结果。');}
$('refresh').onclick=()=>loadLibrary().catch(e=>message(e.message));$('search').oninput=drawLibrary;$('showTrash').onchange=drawLibrary;
$('importNote').onchange=async()=>{try{if(!authenticated)await refreshSession();for(const file of $('importNote').files){if(file.size>500000)throw new Error('单份笔记不能超过 500 KB。');const note=await file.text(),digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(file.name+'\0'+note));const id=Array.from(new Uint8Array(digest)).map(n=>n.toString(16).padStart(2,'0')).join('').slice(0,32);const heading=note.match(/^#\s+(.+)$/m);await api('/import-note',{id,title:(heading?.[1]||file.name).slice(0,200),note});}await loadLibrary();toast('旧笔记已导入，未调用模型');}catch(e){message(e.message);}finally{$('importNote').value='';}};
$('cloudList').onclick=async event=>{const button=event.target.closest('button');if(!button)return;try{if(button.dataset.open)await openNote(button.dataset.open);if(button.dataset.regenerate){if(!confirm('使用已有转写重新生成笔记并补中文？当前正文会保存到历史版本。'))return;button.disabled=true;await api('/records/'+button.dataset.regenerate+'/regenerate',{});await loadLibrary();}if(button.dataset.retry){button.disabled=true;await api('/records/'+button.dataset.retry+'/submit',{});await loadLibrary();}if(button.dataset.trash){const r=records.find(r=>r.id===button.dataset.trash);if(!r.deleted&&!confirm('移入回收站？原音频和笔记可恢复。'))return;await api('/records/'+r.id+'/trash',{deleted:!r.deleted});await loadLibrary();}if(button.dataset.purge){const r=records.find(r=>r.id===button.dataset.purge);if(!confirm('彻底删除“'+r.title+'”？原音频、转写、笔记和历史版本将无法恢复。'))return;button.disabled=true;await api('/records/'+r.id+'/purge',{});await loadLibrary();toast('记录已彻底删除，存储空间已释放');}}catch(e){message(e.message);button.disabled=false;}};
async function openNote(id){current=await api('/records/'+id);editing=false;$('editor').hidden=true;$('noteBody').hidden=false;$('save').hidden=true;$('edit').hidden=false;$('noteTitle').textContent=current.title;$('noteState').textContent=(labels[current.state]||'')+(current.error?' · '+current.error:'');let text=current.note||current.transcript||'音频已保留，等待转写完成。';if(text.split('\n')[0].trim()==='# '+current.title)text=text.slice(text.indexOf('\n')+1);$('noteBody').innerHTML=markdown(text);show('reader');}
$('edit').onclick=async()=>{if(!current)return;if(['queued','processing'].includes(current.state))return message('请等待生成结束再编辑。');const draft=await dbRead('drafts',current.id);$('editTitle').value=draft?.title??current.title;$('editText').value=draft?.note??current.note;current.editVersion=draft?.version??current.version;editing=true;$('editor').hidden=false;$('noteBody').hidden=true;$('save').hidden=false;$('edit').hidden=true;if(draft)message('已恢复此设备的编辑草稿。');};
async function draft(){if(current&&editing)await dbWrite('drafts',{id:current.id,title:$('editTitle').value,note:$('editText').value,version:current.editVersion}).catch(()=>message('编辑草稿未能保存到设备，请复制备份。'));}
function replaceTitle(text,title){const lines=text.split('\n');let fence=false;for(let i=0;i<lines.length;i++){if(/^```/.test(lines[i])){fence=!fence;continue;}if(!fence&&/^#\s/.test(lines[i])){lines[i]='# '+title;return lines.join('\n');}}return '# '+title+'\n\n'+text;}
$('editTitle').oninput=()=>{$('editText').value=replaceTitle($('editText').value,$('editTitle').value);draft();};$('editText').oninput=draft;
$('save').onclick=async()=>{const text=$('editText').value;if(!text.trim()&&current.note&&!confirm('正文为空，保存后将清空整篇笔记，页面上无法恢复。确定继续？'))return;try{await draft();await api('/records/'+current.id+'/edit',{title:$('editTitle').value,note:text,version:current.editVersion,allow_empty:!text.trim()});const db=await database;await new Promise((resolve,reject)=>{const tx=db.transaction('drafts','readwrite');tx.objectStore('drafts').delete(current.id);tx.oncomplete=resolve;tx.onerror=()=>reject(tx.error);});await openNote(current.id);await loadLibrary();toast('标题和正文已保存');}catch(e){message(e.message);}};
$('copy').onclick=async()=>{try{await navigator.clipboard.writeText(editing?$('editText').value:current.note||current.transcript);toast('已复制');}catch{message('浏览器不允许复制，请使用导出按钮。');}};
$('export').onclick=()=>{if(current)download(new Blob([editing?$('editText').value:current.note||current.transcript],{type:'text/markdown'}),current.title+'.md');};
window.addEventListener('online',async()=>{try{await refreshSession();await resumeUploads();}catch(e){message(e.message);}});
window.addEventListener('offline',()=>{$('connection').textContent='离线 · 录音保存在设备上';message('网络已断开。本地录音可继续，已开始的上传会在恢复联网后续传。');});
setInterval(()=>{if(authenticated&&!document.hidden)loadLibrary().catch(()=>{});},5000);
(async()=>{try{await localList();await refreshSession();await loadLibrary();await resumeUploads();}catch(e){$('loginPanel').hidden=navigator.onLine?false:true;$('workspace').hidden=navigator.onLine?true:false;$('connection').textContent=navigator.onLine?'请登录':'离线 · 本地录音可用';if(!navigator.onLine)message('离线模式：可以保存录音，联网登录后再上传。');}if('serviceWorker' in navigator)navigator.serviceWorker.register('/sw.js').catch(()=>{});})();
