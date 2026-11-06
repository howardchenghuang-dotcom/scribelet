'use strict';
const $=id=>document.getElementById(id), MAX=256*1024*1024, PART=2*1024*1024;
// Interface language. Chinese strings in the code are the keys; English is looked up here.
// Default follows the browser (Chinese browsers get Chinese), and the sidebar switch overrides it per device.
function uiLanguage(){
  let saved=null;try{saved=localStorage.getItem('scribelet-ui');}catch{}
  if(saved==='zh'||saved==='en')return saved;
  const browser=(typeof navigator!=='undefined'&&navigator.language)||'zh';
  return /^zh/i.test(browser)?'zh':'en';
}
const UI=uiLanguage(),LOCALE=UI==='zh'?'zh-CN':undefined;
const EN={
  // capture and local recordings
  '已经 {0} 秒几乎没有声音。现场录音请靠近讲台或提高输入音量；网课请确认共享时勾选了声音。':'Almost no sound for {0} seconds. In a room, move closer to the speaker or raise the input volume; for an online class, make sure you shared the tab with audio.',
  '需要登录或重试':'Sign in or try again',
  '离线 · 本地保存可用':'Offline · saving on this device still works',
  '操作失败':'Something went wrong',
  '请求超时，设备上的录音仍在，可继续上传。':'The request timed out. The recording is still on this device and the upload can continue.',
  '本地保存失败':'Could not save on this device',
  '录音达到 256 MB 上限，请结束并开始下一段。':'This recording reached the 256 MB limit. Stop it and start a new one.',
  '本地空间不足':'Not enough storage on this device',
  '本地录音分片缺失，请使用已下载的备份。':'Part of this recording is missing on the device. Use the backup you downloaded.',
  '课堂记录 {0}':'Lecture {0}',
  '录音中':'Recording',
  '已保存到设备':'Saved on this device',
  '录音可能中断，已保留可恢复片段':'May have been interrupted; the recoverable part is kept',
  ' · 已上传云端':' · uploaded',
  '下载备份':'Download backup',
  '查看云端记录':'View note',
  '上传并生成':'Upload & generate',
  '还没有本地录音。开始录音，或导入已有文件。':'No recordings on this device yet. Start recording, or import a file.',
  '已连接私人空间':'Connected to your server',
  '{0} · 模型密钥仅保存在服务器':'{0} · the API key stays on the server',
  '服务器尚未配置模型密钥，录音可以保存，生成将在配置后可用。':'No API key is configured on the server yet. Recordings are saved; notes can be generated once a key is set.',
  '每日上限：{0} 小时转写、{1} 次生成尝试；超限暂停。':'Daily limits: {0} hours of transcription and {1} generation attempts; work pauses when reached.',
  '本地录音模式。上传和生成时需要登录，云端不可用也可以先下载录音备份。':'Local recording mode. You need to sign in to upload and generate; if the server is unreachable, download a backup of the recording.',
  '请先结束录音或等待上传结束。':'Stop recording or wait for the upload to finish first.',
  '已退出。此设备上的录音副本仍保留，请勿在共用设备上使用。':'Signed out. Recordings stay on this device, so do not use a shared computer.',
  '让其他笔记本或 iPad 退出 Scribelet 登录？当前设备不受影响。\n注意：这只撤销 Scribelet 的登录，不影响 Cloudflare 的邮箱验证会话；对方设备下次进入仍需输入 Scribelet 密码。':'Sign out of Scribelet on your other laptops and iPads? This device stays signed in.\nNote: this only ends Scribelet sessions, not any sign-in in front of it (such as Cloudflare Access). Those devices will need the Scribelet password next time.',
  '其他设备已退出登录':'Other devices have been signed out',
  '此浏览器不能录音，请使用系统录音后导入文件。':'This browser cannot record. Record with your system recorder, then import the file.',
  '此设备不支持网页共享声音，请使用系统录屏后导入。':'This device cannot share tab audio. Use screen recording, then import the file.',
  '没有共享音频。请共享浏览器标签页并勾选声音；iPad 请改为导入录屏文件。':'No audio was shared. Share a browser tab and tick "share audio"; on iPad, import a screen recording instead.',
  '录音保存出现问题：{0} 已保存片段可下载，请立即用系统录音接续。':'Problem saving the recording: {0} The saved part can be downloaded. Continue with your system recorder now.',
  '录音被浏览器中断，已保存片段仍在。请下载检查后重新录制。':'The browser interrupted the recording. The saved part is still here; download and check it, then record again.',
  '需要检查录音':'Check this recording',
  '录音已保存，正在自动上传并生成笔记。':'Recording saved. Uploading and generating notes now.',
  '录音可能不完整，请下载已保存片段检查。':'The recording may be incomplete. Download the saved part to check it.',
  '录音已保存在此设备。不足 1 分钟不会自动上传，需要的话点“上传并生成”。':'Recording saved on this device. Recordings under 1 minute are not uploaded automatically; press "Upload & generate" if you want notes.',
  '音频来源已结束，正在保存录音。':'The audio source ended. Saving the recording.',
  '正在录音':'Recording',
  '页面曾被系统暂停，录音可能存在间断，请检查音频备份。':'The system paused this page, so the recording may have gaps. Check the audio backup.',
  '页面进入后台，系统可能暂停录音。请返回此页面并检查声音。':'This page went to the background and the system may pause recording. Come back to this page and check the sound.',
  '文件必须大于 0 字节且小于 256 MB。':'The file must be larger than 0 bytes and smaller than 256 MB.',
  '文件已保存，正在自动上传并生成笔记。':'File saved. Uploading and generating notes now.',
  '导入失败：{0}':'Import failed: {0}',
  '这段录音可能曾中断。仍上传已保存部分进行恢复？':'This recording may have been interrupted. Upload the saved part anyway?',
  '没有已保存的音频。':'There is no saved audio.',
  '正在上传 {0}% · 设备上的录音副本已保留':'Uploading {0}% · the copy on this device is kept',
  '录音已上传，请在“我的笔记”查看生成状态；如需重试，请使用记录上的重试按钮。':'Uploaded. Check progress in "My notes"; to try again, use the retry button on the note.',
  '上传中断可继续，原音频仍在此设备。':'The upload can resume; the original audio is still on this device.',
  // library and reader
  '阅读':'Read','重新生成笔记':'Regenerate note','重试生成':'Retry','恢复':'Restore','删除':'Delete','彻底删除':'Delete forever',
  '这里还没有记录。':'Nothing here yet.',
  '录音已上传':'Uploaded',
  '处理已结束，请在“我的笔记”查看结果。':'Processing finished. See the result in "My notes".',
  '单份笔记不能超过 500 KB。':'Each note must be under 500 KB.',
  '旧笔记已导入，未调用模型':'Notes imported; no model was called',
  '使用已有转写重新生成笔记并补中文？当前正文会保存到历史版本。':'Regenerate this note from the saved transcript, filling in missing translations? The current text is kept in the version history.',
  '移入回收站？原音频和笔记可恢复。':'Move to trash? The audio and note can be restored.',
  '彻底删除“{0}”？原音频、转写、笔记和历史版本将无法恢复。':'Delete "{0}" forever? The audio, transcript, note and version history cannot be recovered.',
  '记录已彻底删除，存储空间已释放':'Deleted forever; the storage has been freed',
  '音频已保留，等待转写完成。':'The audio is saved; waiting for transcription to finish.',
  '请等待生成结束再编辑。':'Wait for generation to finish before editing.',
  '已恢复此设备的编辑草稿。':'Restored your unsaved draft from this device.',
  '编辑草稿未能保存到设备，请复制备份。':'Could not save the draft on this device; copy your text somewhere safe.',
  '正文为空，保存后将清空整篇笔记，页面上无法恢复。确定继续？':'The body is empty. Saving will erase the whole note and it cannot be undone here. Continue?',
  '标题和正文已保存':'Title and note saved',
  '已复制':'Copied',
  '浏览器不允许复制，请使用导出按钮。':'The browser blocked copying; use Export instead.',
  '离线 · 录音保存在设备上':'Offline · recordings are saved on this device',
  '网络已断开。本地录音可继续，已开始的上传会在恢复联网后续传。':'You are offline. Recording continues on this device, and uploads resume when you reconnect.',
  '请登录':'Please sign in',
  '离线 · 本地录音可用':'Offline · local recording available',
  '离线模式：可以保存录音，联网登录后再上传。':'Offline mode: recordings are saved; sign in when online to upload.',
  '上传未完成':'Upload incomplete','等待生成':'Waiting','正在转写和整理':'Transcribing and writing notes','笔记已生成':'Note ready','已保存 · 等待重试':'Saved · waiting to retry',
  // page
  'Scribelet · 私人课堂笔记':'Scribelet · lecture notes','你的私人学习空间':'Your private study space','课堂记录':'Record','我的笔记':'My notes',
  '仅本人访问':'Only you can access this','正在连接…':'Connecting…','学习工作台':'Workspace','/ 随行版':'/ on the go',
  '退出其他设备的 Scribelet 登录':'Sign out other devices','退出登录':'Sign out','欢迎回来。':'Welcome back.',
  '只有你的私人账号可以访问笔记和使用模型额度。':'Only your account can open these notes and use the model budget.',
  '账号':'Login','密码':'Password','登录私人空间':'Sign in','网络不稳定？先在此设备录音':'Unstable network? Record on this device first',
  '本地录音无需调用模型。上传和生成必须登录。':'Recording locally does not call any model. Uploading and generating need you to sign in.',
  '专注这一课。':'Focus on the lecture.','先留下声音，再整理成双语对照笔记。':'Capture the sound now; get bilingual notes after class.',
  '准备就绪':'Ready','课程 / 记录名称':'Course / title','例如：Strategy · Week 3':'e.g. Strategy · Week 3',
  '课堂语言':'Lecture language','自动识别':'Auto-detect','笔记语言':'Note language',
  '笔记按「课堂语言 → 笔记语言」逐条对照；两者相同时只写一种语言。':'Notes pair the lecture language with the note language line by line; choose the same language for one-language notes.',
  '声音来源':'Audio source','网课声音 · 共享浏览器标签页':'Online class · share a browser tab','现场声音 · 麦克风':'In the room · microphone',
  '输入音量':'Input level','开始录音':'Start recording','结束并保存录音':'Stop and save',
  '录音期间保持页面在前台。iPad 内部网课声音请优先用系统录屏或课程音频文件，再导入。':'Keep this page in front while recording. On iPad, capture online classes with screen recording or the course audio file, then import it.',
  '这台设备的浏览器不能直接录网课声音。上课前从右上角下拉打开控制中心，点「屏幕录制」（可按住选择是否同时开麦克风）。':'This browser cannot record another app\'s audio. Before class, swipe down from the top-right corner to open Control Center and tap Screen Recording (press and hold to choose whether to include the microphone).',
  '正常听课；锁屏或切到别的 App 可能会中断录制，尽量保持在课程页面。':'Attend the class as usual. Locking the screen or switching apps may stop the recording, so stay on the class page.',
  '下课后再打开控制中心点停止，录像会自动存进「照片」。先播放前 10 秒确认有声音——受保护的课程视频可能录不到声音。':'After class, open Control Center again and stop. The video is saved to Photos. Play the first 10 seconds to check the sound — protected course videos may record silently.',
  '回到这里点「导入文件」，从「照片」或「文件」里选那段录像，就会生成同一份双语笔记。':'Come back here, tap "Import file" and pick the video from Photos or Files to get the same bilingual notes.',
  '已有录音或录屏？':'Already have a recording?','导入音频或视频，生成同一份双语笔记。每份不超过 256 MB / 4 小时。':'Import audio or video to get the same bilingual notes. Up to 256 MB / 4 hours each.',
  '导入文件':'Import file','设备上的录音':'Recordings on this device','断网时仍可保存 · 上传后保留本地副本':'Saves offline · a local copy is kept after upload',
  '录音保存与云端生成分开进行':'Recording and note generation are separate steps',
  '结束录音后，点击“上传并生成”。上传中断可继续，生成失败可重试。清理浏览器数据会删除尚未上传的本地录音，请及时下载备份。iPad 锁屏或切换应用可能中断网页录音，重要课程请同时用系统录音保存原文件。':'After recording, press "Upload & generate". Interrupted uploads resume and failed generations can be retried. Clearing browser data deletes recordings that have not been uploaded, so download a backup. On iPad, locking the screen or switching apps may stop web recording; for important classes, also record with the system recorder.',
  '导入旧笔记':'Import notes','刷新记录':'Refresh','搜索课程标题':'Search titles','回收站':'Trash','← 返回笔记':'← Back to notes',
  '复制全文':'Copy all','导出':'Export','编辑标题和正文':'Edit','保存修改':'Save changes','笔记标题':'Title','笔记正文':'Note',
  '编辑草稿保存在当前设备。保存后同步到云端。':'Drafts are kept on this device and synced to your server when you save.',
  // messages from the server
  '录音分片在下载过程中消失，已中止传输':'Part of the recording disappeared during the download; stopped.',
  '录音分片在下载过程中改变，已中止传输':'Part of the recording changed during the download; stopped.',
  '异地备份尚未成功；请在 Mac 上检查备份任务。':'The off-site backup has not succeeded yet; check the backup job on the Mac.',
  '异地备份时间在未来；请检查 Mac 时钟。':'The off-site backup time is in the future; check the Mac clock.',
  '异地备份已超过 26 小时未成功；请检查 Mac。':'No successful off-site backup for over 26 hours; check the Mac.',
  '异地备份状态文件损坏；请在 Mac 上检查备份任务。':'The off-site backup status file is damaged; check the backup job on the Mac.',
  '记录不存在':'Not found','今日用量已达上限，录音已保留，请次日重试。':'Today\'s limit is reached. The recording is kept; try again tomorrow.',
  '内容超过大小限制':'Too large','请求格式错误':'Malformed request','站点地址不匹配':'Wrong site address','请登录你的私人账号':'Please sign in',
  '请求来源不匹配':'Request origin does not match','登录状态校验失败，请刷新后重试':'Session check failed; refresh and try again',
  '尝试次数过多，请稍后再试':'Too many attempts; try again later','账号或密码不正确':'Wrong login or password',
  '导入笔记格式或长度无效':'Invalid note format or length','导入标识冲突':'Import ID conflict','笔记数量已达上限':'Note limit reached',
  '不支持的语言':'Unsupported language','文件信息无效；单份最大 256 MB':'Invalid file; 256 MB maximum',
  '上传标识已被其他文件占用':'This upload ID belongs to another file','存储空间已达上限，请备份后联系管理员清理':'Storage is full; back up and free some space',
  '分片编号无效':'Invalid part number','分片长度不匹配':'Part size does not match','记录已提交或已删除':'Already submitted or deleted',
  '分片内容冲突，原数据未覆盖':'Part content conflict; nothing was overwritten','提交标识无效':'Invalid submission ID','请先恢复记录':'Restore it first',
  '录音尚未上传完整':'The recording has not finished uploading','需要已有音频转写才能重新生成笔记':'Regenerating needs an existing transcript',
  '标题或正文长度无效':'Invalid title or note length','内容已更新或正在生成，请重新打开；你的编辑仍在输入框内':'The note changed or is being generated; reopen it. Your edits are still in the editor.',
  '正文为空会清空整篇笔记。请在页面上确认后重试，或把整条记录移入回收站。':'An empty body erases the whole note. Confirm on the page and try again, or move the note to trash.',
  '删除状态无效':'Invalid delete state','请等待生成结束':'Wait for generation to finish','请先把记录移入回收站':'Move it to trash first',
  '这是一条纯文本笔记，没有原始音频。':'This is a text-only note; there is no audio.','录音已改变，请重新打开后下载。':'The recording changed; reopen it and download again.',
  '录音尚未上传完整，请先续传完成再下载原音频。':'The recording has not finished uploading; resume the upload before downloading.','页面不存在':'Page not found',
  '服务器缺少音频处理组件，录音已保留。':'The server is missing its audio tools (ffmpeg); the recording is kept.',
  '无法读取这份音频。请先用系统导出或转换成 WAV / MP3 后重新导入；原文件已保留。':'Could not read this audio. Export or convert it to WAV / MP3 and import again; the original is kept.',
  '单次录音须大于 0 秒且不超过 4 小时，原文件已保留。':'A recording must be longer than 0 seconds and at most 4 hours; the original is kept.',
  '尚未配置模型服务密钥，录音已保留。':'No API key is configured; the recording is kept.',
  '模型服务密钥无效':'The API key was rejected','模型账户余额不足':'The model account is out of credit','模型服务暂时限流':'The model service is rate-limiting',
  '模型服务暂时不可用':'The model service is unavailable','录音已保存，可稍后重试。':'the recording is saved; try again later.',
  '转写返回格式异常，原音频已保留。':'The transcription came back malformed; the audio is kept.',
  '笔记返回格式异常，转写和音频已保留，可重试。':'The note came back malformed; the transcript and audio are kept. You can retry.',
  '笔记输出不完整，转写和音频已保留，可重试。':'The note was cut off; the transcript and audio are kept. You can retry.',
  '翻译返回格式异常或缺少编号':'The translation came back malformed or incomplete',
  '没有识别到语音，未调用笔记模型。请下载原音频检查声音。':'No speech was recognised, so no note was generated. Download the audio and check the sound.',
  '转写过长，请拆分录音；全文及音频已保留。':'The transcript is too long; split the recording. The transcript and audio are kept.',
  '上传尚未完成':'The upload has not finished',
  '音频转换时长变化，不能安全复用转写切分；请检查原音频。':'The converted audio length changed, so earlier segments cannot be reused safely; check the original audio.',
  '旧转写没有切分计划，不能安全续跑；请导出转写或重新导入音频。':'This older transcript has no segment plan and cannot resume safely; export the transcript or import the audio again.',
  '服务重启中断了任务，录音和已完成转写仍在，请点击重试。':'A server restart interrupted this job. The recording and finished transcription are kept; press Retry.',
  '模型服务响应超时，音频和已完成转写已保存。可稍后重试；不会自动重复调用。':'The model service timed out. The audio and finished transcription are saved; retry later (nothing is retried automatically).',
  '暂时无法连接模型服务，音频和已完成转写已保存，请稍后重试。':'Cannot reach the model service right now. The audio and finished transcription are saved; try again later.',
  '处理失败，录音和已完成转写已保存，请稍后重试。':'Processing failed. The recording and finished transcription are saved; try again later.',
};
// Server messages are sometimes two sentences joined with "；"; translate each half.
function t(text,...args){
  if(typeof text!=='string')return text;
  let out=text;
  if(UI==='en'){
    if(EN[text]!==undefined)out=EN[text];
    else if(text.includes('；'))out=text.split('；').map(part=>EN[part]??part).join('; ');
  }
  return args.reduce((s,value,i)=>s.split('{'+i+'}').join(String(value)),out);
}
// Static page text: swap every text node and placeholder that has an English entry.
function translatePage(){
  const switcher=$('uiLang');
  if(switcher){switcher.textContent=UI==='en'?'中文':'English';switcher.onclick=()=>{try{localStorage.setItem('scribelet-ui',UI==='en'?'zh':'en');}catch{}location.reload();};}
  if(UI!=='en'||typeof document.createTreeWalker!=='function')return;
  document.documentElement.lang='en';document.title=t(document.title);
  const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);
  for(let node=walker.nextNode();node;node=walker.nextNode()){const key=node.nodeValue.trim();if(key&&EN[key]!==undefined)node.nodeValue=node.nodeValue.replace(key,EN[key]);}
  for(const element of document.querySelectorAll('[placeholder],[aria-label]'))for(const name of ['placeholder','aria-label']){const value=element.getAttribute(name);if(value&&EN[value]!==undefined)element.setAttribute(name,EN[value]);}
}
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
  // First visit: notes in the interface language (Chinese interface → Chinese notes).
  if(!saved&&$('targetLang'))$('targetLang').value=UI;
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
const QUIET_MESSAGE=t('已经 {0} 秒几乎没有声音。现场录音请靠近讲台或提高输入音量；网课请确认共享时勾选了声音。',QUIET_SECONDS);
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
function message(text){text=t(text);$('notice').textContent=text;$('notice').hidden=!text;}
function toast(text){$('toast').textContent=t(text);$('toast').hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('toast').hidden=true,5000);}
async function api(path,body,method,timeoutMs=60000){
  const controller=new AbortController(), timeout=setTimeout(()=>controller.abort(),timeoutMs);
  try{
    const response=await fetch('/api'+path,{method:method||(body===undefined?'GET':'POST'),credentials:'same-origin',cache:'no-store',signal:controller.signal,headers:body===undefined?{}:{'Content-Type':body instanceof Blob?'application/octet-stream':'application/json','X-CSRF-Token':csrf},body:body===undefined?undefined:body instanceof Blob?body:JSON.stringify(body)});
    const result=await response.json();
    if(!response.ok){if(response.status===401){authenticated=false;csrf='';$('loginPanel').hidden=false;if(!recording)$('workspace').hidden=true;}$('connection').textContent=navigator.onLine?t('需要登录或重试'):t('离线 · 本地保存可用');throw new Error(result.detail||t('操作失败'));}
    return result;
  }catch(error){if(error.name==='AbortError')throw new Error(t('请求超时，设备上的录音仍在，可继续上传。'));throw error;}finally{clearTimeout(timeout);}
}
const database=new Promise((resolve,reject)=>{
  const request=indexedDB.open('scribelet',1);
  request.onupgradeneeded=()=>{for(const name of ['recordings','chunks','drafts'])request.result.createObjectStore(name,{keyPath:'id'});};
  request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);
});
async function dbRead(store,id){const db=await database;return new Promise((resolve,reject)=>{const request=db.transaction(store).objectStore(store)[id===undefined?'getAll':'get'](id);request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);});}
async function dbWrite(store,value){const db=await database;return new Promise((resolve,reject)=>{const tx=db.transaction(store,'readwrite');tx.objectStore(store).put(value);tx.oncomplete=resolve;tx.onerror=()=>reject(tx.error);tx.onabort=()=>reject(tx.error||new Error(t('本地保存失败')));});}
async function appendChunk(id,blob){
  const record=await dbRead('recordings',id);
  if(record.size+blob.size>MAX)throw new Error(t('录音达到 256 MB 上限，请结束并开始下一段。'));
  const db=await database;
  await new Promise((resolve,reject)=>{const tx=db.transaction(['recordings','chunks'],'readwrite');tx.objectStore('chunks').put({id:id+':'+record.count,blob});record.count++;record.size+=blob.size;tx.objectStore('recordings').put(record);tx.oncomplete=resolve;tx.onerror=()=>reject(tx.error);tx.onabort=()=>reject(tx.error||new Error(t('本地空间不足')));});
}
async function recordingBlob(record){const chunks=[];for(let i=0;i<record.count;i++){const chunk=await dbRead('chunks',record.id+':'+i);if(!chunk)throw new Error(t('本地录音分片缺失，请使用已下载的备份。'));chunks.push(chunk.blob);}return new Blob(chunks,{type:record.mime});}
function uid(){return crypto.randomUUID().replace(/-/g,'');}
function title(){return $('title').value.trim()||t('课堂记录 {0}',new Date().toLocaleString(LOCALE));}
function extension(mime){if(mime.includes('mp4'))return 'm4a';if(mime.includes('mpeg'))return 'mp3';if(mime.includes('wav'))return 'wav';return 'webm';}
function download(blob,name){const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),30000);}
async function localList(){
  const local=(await dbRead('recordings')).sort((a,b)=>b.created-a.created);
  $('localList').innerHTML=local.map(r=>`<div class="row"><div><strong>${esc(r.title)}</strong><small>${(r.size/1024/1024).toFixed(1)} MB · ${r.id===recording?t('录音中'):r.complete?t('已保存到设备'):t('录音可能中断，已保留可恢复片段')}${r.uploaded?t(' · 已上传云端'):''}</small></div><div class="actions"><button data-local-download="${r.id}" ${r.id===recording?'disabled':''}>${t('下载备份')}</button><button data-upload="${r.id}" ${r.id===recording||uploading?'disabled':''}>${r.uploaded?t('查看云端记录'):t('上传并生成')}</button></div></div>`).join('')||'<div class="empty">'+t('还没有本地录音。开始录音，或导入已有文件。')+'</div>';
}
async function refreshSession(){const me=await api('/me');csrf=me.csrf;authenticated=true;$('loginPanel').hidden=true;$('workspace').hidden=false;$('logout').hidden=false;$('revoke').hidden=false;$('connection').textContent=t('已连接私人空间');$('accountStatus').textContent=me.key_ready?t('{0} · 模型密钥仅保存在服务器',me.model):t('服务器尚未配置模型密钥，录音可以保存，生成将在配置后可用。');if(me.limits)$('accountStatus').textContent+=' '+t('每日上限：{0} 小时转写、{1} 次生成尝试；超限暂停。',me.limits.audio_seconds/3600,me.limits.jobs);if(me.backup_warning){$('accountStatus').textContent+=' ⚠ '+me.backup_warning;$('accountStatus').classList?.add('warning');}return me;}
translatePage();restoreSource();restoreLanguages();
$('loginForm').onsubmit=async event=>{event.preventDefault();const button=event.submitter;button.disabled=true;try{await api('/login',{username:$('username').value,password:$('password').value});$('password').value='';await refreshSession();await loadLibrary();await resumeUploads();message('');}catch(e){message(e.message);}finally{button.disabled=false;}};
$('localOnly').onclick=()=>{$('loginPanel').hidden=true;$('workspace').hidden=false;show('captureView');message(t('本地录音模式。上传和生成时需要登录，云端不可用也可以先下载录音备份。'));};
$('logout').onclick=async()=>{if(recording||uploading)return message(t('请先结束录音或等待上传结束。'));try{await api('/logout',{});authenticated=false;csrf='';records=[];current=null;$('noteBody').textContent='';$('cloudList').textContent='';$('workspace').hidden=true;$('loginPanel').hidden=false;$('logout').hidden=$('revoke').hidden=true;message(t('已退出。此设备上的录音副本仍保留，请勿在共用设备上使用。'));}catch(e){message(e.message);}};
$('revoke').onclick=async()=>{if(!confirm(t('让其他笔记本或 iPad 退出 Scribelet 登录？当前设备不受影响。\n注意：这只撤销 Scribelet 的登录，不影响 Cloudflare 的邮箱验证会话；对方设备下次进入仍需输入 Scribelet 密码。')))return;try{await api('/revoke-others',{});toast('其他设备已退出登录');}catch(e){message(e.message);}};

async function startRecording(){
  $('start').disabled=true;message('');
  try{
    if(!navigator.mediaDevices||!window.MediaRecorder)throw new Error(t('此浏览器不能录音，请使用系统录音后导入文件。'));
    if(navigator.storage?.persist)await navigator.storage.persist();
    if($('source').value==='screen'){
      if(!navigator.mediaDevices.getDisplayMedia)throw new Error(t('此设备不支持网页共享声音，请使用系统录屏后导入。'));
      stream=await navigator.mediaDevices.getDisplayMedia({video:true,audio:true});
      if(!stream.getAudioTracks().length){stream.getTracks().forEach(t=>t.stop());throw new Error(t('没有共享音频。请共享浏览器标签页并勾选声音；iPad 请改为导入录屏文件。'));}
    }else stream=await navigator.mediaDevices.getUserMedia({audio:MIC_CONSTRAINTS});
    const audioStream=new MediaStream(stream.getAudioTracks());
    const mime=['audio/webm;codecs=opus','audio/mp4','audio/webm'].find(t=>MediaRecorder.isTypeSupported(t));
    recorder=new MediaRecorder(audioStream,mime?{mimeType:mime}:{});
    const id=uid();recording=id;writing=Promise.resolve();captureError=false;
    const langs=chosenLanguages();await dbWrite('recordings',{id,title:title(),mime:recorder.mimeType||mime||'audio/webm',created:Date.now(),size:0,count:0,complete:false,source_lang:langs.source,target_lang:langs.target});
    recorder.ondataavailable=event=>{if(!event.data.size)return;writing=writing.then(()=>appendChunk(id,event.data)).catch(error=>{captureError=true;message(t('录音保存出现问题：{0} 已保存片段可下载，请立即用系统录音接续。',t(error.message)));if(recorder?.state==='recording')recorder.stop();});};
    recorder.onerror=()=>{captureError=true;message(t('录音被浏览器中断，已保存片段仍在。请下载检查后重新录制。'));if(recorder?.state==='recording')recorder.stop();};
    recorder.onstop=async()=>{stream?.getTracks().forEach(t=>t.stop());await writing;const local=await dbRead('recordings',id);if(local){local.complete=!captureError;await dbWrite('recordings',local);}recording=null;$('start').hidden=false;$('stop').hidden=true;$('recordState').textContent=captureError?t('需要检查录音'):t('已保存到设备');$('recordState').className='badge';clearInterval(startRecording.clock);await meterContext?.close();meterContext=null;await wakeLock?.release();wakeLock=null;await localList();if(shouldAutoUpload(Date.now()-started,captureError)){if(local){local.autoUpload=true;await dbWrite('recordings',local);}toast('录音已保存，正在自动上传并生成笔记。');await upload(id);}else toast(captureError?t('录音可能不完整，请下载已保存片段检查。'):t('录音已保存在此设备。不足 1 分钟不会自动上传，需要的话点“上传并生成”。'));};
    stream.getAudioTracks()[0].onended=()=>{if(recorder?.state==='recording'){message(t('音频来源已结束，正在保存录音。'));recorder.stop();}};
    recorder.start(5000);$('start').hidden=true;$('stop').hidden=false;$('recordState').textContent=t('正在录音');$('recordState').className='badge recording';
    const started=Date.now();let last=started;
    try{wakeLock=await navigator.wakeLock?.request('screen');}catch{}
    try{meterContext=new AudioContext();const analyser=meterContext.createAnalyser();meterContext.createMediaStreamSource(audioStream).connect(analyser);startRecording.analyser=analyser;}catch{startRecording.analyser=null;}
    startRecording.clock=setInterval(()=>{const now=Date.now(),seconds=Math.floor((now-started)/1000);$('timer').textContent=Math.floor(seconds/60).toString().padStart(2,'0')+':'+(seconds%60).toString().padStart(2,'0');if(now-last>15000){captureError=true;message(t('页面曾被系统暂停，录音可能存在间断，请检查音频备份。'));}last=now;if(startRecording.analyser){const data=new Uint8Array(startRecording.analyser.fftSize);startRecording.analyser.getByteTimeDomainData(data);const rms=Math.sqrt(data.reduce((s,n)=>s+(n-128)**2,0)/data.length);$('level').value=Math.min(100,rms*5);startRecording.quiet=quietStreak(rms,startRecording.quiet||0);if(startRecording.quiet===QUIET_SECONDS)message(QUIET_MESSAGE);}if(seconds>=14400&&recorder?.state==='recording')recorder.stop();},1000);
    await localList();
  }catch(e){stream?.getTracks().forEach(t=>t.stop());recording=null;message(e.message);}finally{$('start').disabled=false;}
}
$('start').onclick=startRecording;$('stop').onclick=()=>{if(recorder?.state==='recording'){recorder.stop();$('stop').disabled=true;writing.finally(()=>$('stop').disabled=false);}};
document.addEventListener('visibilitychange',()=>{if(document.hidden&&recording)message(t('页面进入后台，系统可能暂停录音。请返回此页面并检查声音。'));});
window.addEventListener('beforeunload',event=>{if(recording||uploading){event.preventDefault();event.returnValue='';}});
$('file').onchange=async()=>{const file=$('file').files[0];if(!file)return;try{if(!file.size||file.size>MAX)throw new Error(t('文件必须大于 0 字节且小于 256 MB。'));const id=uid(),langs=chosenLanguages();await dbWrite('recordings',{id,title:$('title').value.trim()||file.name,mime:file.type||'application/octet-stream',created:Date.now(),size:0,count:0,complete:false,source_lang:langs.source,target_lang:langs.target});for(let at=0;at<file.size;at+=PART)await appendChunk(id,file.slice(at,at+PART));const local=await dbRead('recordings',id);local.complete=true;local.autoUpload=true;await dbWrite('recordings',local);await localList();toast('文件已保存，正在自动上传并生成笔记。');await upload(id);}catch(e){message(t('导入失败：{0}',t(e.message)));await localList();}finally{$('file').value='';}};
const uploadQueue=new Set();
async function resumeUploads(){for(const r of await dbRead('recordings'))if(r.autoUpload&&!r.uploaded)await upload(r.id);}
async function upload(id){
  if(uploading){uploadQueue.add(id);return;}uploading=true;await localList();
  try{
    if(!authenticated)await refreshSession();
    let local=await dbRead('recordings',id);
    if(local.uploaded){await openNote(id);return;}
    if(!local.complete&&!confirm(t('这段录音可能曾中断。仍上传已保存部分进行恢复？')))return;
    if(!local.size)throw new Error(t('没有已保存的音频。'));
    local.autoUpload=true;await dbWrite('recordings',local);
    const blob=await recordingBlob(local);
    const body={id,title:local.title,size:blob.size,mime:local.mime};if(local.source_lang)body.source_lang=local.source_lang;if(local.target_lang)body.target_lang=local.target_lang;await api('/records',body);
    const server=await api('/records/'+id);
    if(server.state==='uploading')for(let i=0;i<Math.ceil(blob.size/PART);i++){
      if(server.parts.includes(i))continue;
      message(t('正在上传 {0}% · 设备上的录音副本已保留',Math.round(i*PART/blob.size*100)));
      await api('/records/'+id+'/parts/'+i,blob.slice(i*PART,(i+1)*PART),'PUT',300000);
    }
    await api('/records/'+id+'/submit',{submission_id:id});
    local.uploaded=true;local.autoUpload=false;await dbWrite('recordings',local);
    message(t('录音已上传，请在“我的笔记”查看生成状态；如需重试，请使用记录上的重试按钮。'));await loadLibrary();show('libraryView');
  }catch(e){message(t(e.message)+' '+t('上传中断可继续，原音频仍在此设备。'));}finally{uploading=false;await localList();const next=uploadQueue.values().next().value;if(next){uploadQueue.delete(next);await upload(next);}}
}
$('localList').onclick=async event=>{const button=event.target.closest('button');if(!button)return;try{if(button.dataset.upload)await upload(button.dataset.upload);else if(button.dataset.localDownload){const r=await dbRead('recordings',button.dataset.localDownload);download(await recordingBlob(r),r.title+'.'+extension(r.mime));}}catch(e){message(e.message);}};
function show(view){for(const id of ['captureView','libraryView','reader'])$(id).hidden=id!==view;$('homeNav').classList?.toggle('selected',view==='captureView');$('notesNav').classList?.toggle('selected',view!=='captureView');}
$('homeNav').onclick=()=>show('captureView');$('notesNav').onclick=()=>{show('libraryView');loadLibrary().catch(e=>message(e.message));};$('back').onclick=()=>show('libraryView');
const labels={uploading:'上传未完成',queued:'等待生成',processing:'正在转写和整理',done:'笔记已生成',error:'已保存 · 等待重试'};
function drawLibrary(){const query=$('search').value.trim().toLowerCase(),trash=$('showTrash').checked;$('cloudList').innerHTML=records.filter(r=>Boolean(r.deleted)===trash&&r.title.toLowerCase().includes(query)).map(r=>`<div class="row"><div><strong>${esc(r.title)}</strong><small>${new Date(r.created*1000).toLocaleString(LOCALE)} · ${esc(t(labels[r.state]))}${r.state==='processing'?' '+r.progress+'%':''}</small>${r.error?'<p class="muted">'+esc(t(r.error))+'</p>':''}</div><div class="actions"><button data-open="${r.id}">${t('阅读')}</button>${r.state==='done'&&r.size>0&&!r.deleted?`<button data-regenerate="${r.id}">${t('重新生成笔记')}</button>`:''}${r.state==='error'&&!r.deleted?`<button data-retry="${r.id}">${t('重试生成')}</button>`:''}<button data-trash="${r.id}" ${['queued','processing'].includes(r.state)?'disabled':''}>${r.deleted?t('恢复'):t('删除')}</button>${r.deleted?`<button data-purge="${r.id}">${t('彻底删除')}</button>`:''}</div></div>`).join('')||'<div class="empty">'+t('这里还没有记录。')+'</div>';}
async function loadLibrary(){if(!authenticated)return;const next=await api('/records');if(JSON.stringify(next)!==JSON.stringify(records)){records=next;drawLibrary();}if($('notice').textContent.startsWith(t('录音已上传'))&&records.length&&!records.some(r=>['queued','processing'].includes(r.state)))message(t('处理已结束，请在“我的笔记”查看结果。'));}
$('refresh').onclick=()=>loadLibrary().catch(e=>message(e.message));$('search').oninput=drawLibrary;$('showTrash').onchange=drawLibrary;
$('importNote').onchange=async()=>{try{if(!authenticated)await refreshSession();for(const file of $('importNote').files){if(file.size>500000)throw new Error(t('单份笔记不能超过 500 KB。'));const note=await file.text(),digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(file.name+'\0'+note));const id=Array.from(new Uint8Array(digest)).map(n=>n.toString(16).padStart(2,'0')).join('').slice(0,32);const heading=note.match(/^#\s+(.+)$/m);await api('/import-note',{id,title:(heading?.[1]||file.name).slice(0,200),note});}await loadLibrary();toast('旧笔记已导入，未调用模型');}catch(e){message(e.message);}finally{$('importNote').value='';}};
$('cloudList').onclick=async event=>{const button=event.target.closest('button');if(!button)return;try{if(button.dataset.open)await openNote(button.dataset.open);if(button.dataset.regenerate){if(!confirm(t('使用已有转写重新生成笔记并补中文？当前正文会保存到历史版本。')))return;button.disabled=true;await api('/records/'+button.dataset.regenerate+'/regenerate',{});await loadLibrary();}if(button.dataset.retry){button.disabled=true;await api('/records/'+button.dataset.retry+'/submit',{});await loadLibrary();}if(button.dataset.trash){const r=records.find(r=>r.id===button.dataset.trash);if(!r.deleted&&!confirm(t('移入回收站？原音频和笔记可恢复。')))return;await api('/records/'+r.id+'/trash',{deleted:!r.deleted});await loadLibrary();}if(button.dataset.purge){const r=records.find(r=>r.id===button.dataset.purge);if(!confirm(t('彻底删除“{0}”？原音频、转写、笔记和历史版本将无法恢复。',r.title)))return;button.disabled=true;await api('/records/'+r.id+'/purge',{});await loadLibrary();toast('记录已彻底删除，存储空间已释放');}}catch(e){message(e.message);button.disabled=false;}};
async function openNote(id){current=await api('/records/'+id);editing=false;$('editor').hidden=true;$('noteBody').hidden=false;$('save').hidden=true;$('edit').hidden=false;$('noteTitle').textContent=current.title;$('noteState').textContent=t(labels[current.state]||'')+(current.error?' · '+t(current.error):'');let text=current.note||current.transcript||t('音频已保留，等待转写完成。');if(text.split('\n')[0].trim()==='# '+current.title)text=text.slice(text.indexOf('\n')+1);$('noteBody').innerHTML=markdown(text);show('reader');}
$('edit').onclick=async()=>{if(!current)return;if(['queued','processing'].includes(current.state))return message(t('请等待生成结束再编辑。'));const draft=await dbRead('drafts',current.id);$('editTitle').value=draft?.title??current.title;$('editText').value=draft?.note??current.note;current.editVersion=draft?.version??current.version;editing=true;$('editor').hidden=false;$('noteBody').hidden=true;$('save').hidden=false;$('edit').hidden=true;if(draft)message(t('已恢复此设备的编辑草稿。'));};
async function draft(){if(current&&editing)await dbWrite('drafts',{id:current.id,title:$('editTitle').value,note:$('editText').value,version:current.editVersion}).catch(()=>message(t('编辑草稿未能保存到设备，请复制备份。')));}
function replaceTitle(text,title){const lines=text.split('\n');let fence=false;for(let i=0;i<lines.length;i++){if(/^```/.test(lines[i])){fence=!fence;continue;}if(!fence&&/^#\s/.test(lines[i])){lines[i]='# '+title;return lines.join('\n');}}return '# '+title+'\n\n'+text;}
$('editTitle').oninput=()=>{$('editText').value=replaceTitle($('editText').value,$('editTitle').value);draft();};$('editText').oninput=draft;
$('save').onclick=async()=>{const text=$('editText').value;if(!text.trim()&&current.note&&!confirm(t('正文为空，保存后将清空整篇笔记，页面上无法恢复。确定继续？')))return;try{await draft();await api('/records/'+current.id+'/edit',{title:$('editTitle').value,note:text,version:current.editVersion,allow_empty:!text.trim()});const db=await database;await new Promise((resolve,reject)=>{const tx=db.transaction('drafts','readwrite');tx.objectStore('drafts').delete(current.id);tx.oncomplete=resolve;tx.onerror=()=>reject(tx.error);});await openNote(current.id);await loadLibrary();toast('标题和正文已保存');}catch(e){message(e.message);}};
$('copy').onclick=async()=>{try{await navigator.clipboard.writeText(editing?$('editText').value:current.note||current.transcript);toast('已复制');}catch{message(t('浏览器不允许复制，请使用导出按钮。'));}};
$('export').onclick=()=>{if(current)download(new Blob([editing?$('editText').value:current.note||current.transcript],{type:'text/markdown'}),current.title+'.md');};
window.addEventListener('online',async()=>{try{await refreshSession();await resumeUploads();}catch(e){message(e.message);}});
window.addEventListener('offline',()=>{$('connection').textContent=t('离线 · 录音保存在设备上');message(t('网络已断开。本地录音可继续，已开始的上传会在恢复联网后续传。'));});
setInterval(()=>{if(authenticated&&!document.hidden)loadLibrary().catch(()=>{});},5000);
(async()=>{try{await localList();await refreshSession();await loadLibrary();await resumeUploads();}catch(e){$('loginPanel').hidden=navigator.onLine?false:true;$('workspace').hidden=navigator.onLine?true:false;$('connection').textContent=navigator.onLine?t('请登录'):t('离线 · 本地录音可用');if(!navigator.onLine)message(t('离线模式：可以保存录音，联网登录后再上传。'));}if('serviceWorker' in navigator)navigator.serviceWorker.register('/sw.js').catch(()=>{});})();
