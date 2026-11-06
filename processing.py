"""Durable single-consumer audio jobs with conservative, pre-call quota reservation."""
import hashlib
import json
import math
import re
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import wave

import httpx
from bilingual import build_summary_prompt, build_staged_summary_prompt, build_translation_prompt


def ffmpeg():
    executable = os.getenv('SCRIBELET_FFMPEG') or shutil.which('ffmpeg')
    if not executable:
        raise ValueError('服务器缺少音频处理组件，录音已保留。')
    return executable


def convert(source, output):
    # Read a local upload only: disallow playlists from fetching network/file URLs.
    result = subprocess.run([ffmpeg(),'-nostdin','-v','error','-protocol_whitelist','file,pipe',
        '-format_whitelist','mov,mp3,wav,ogg,flac,matroska,webm,aac,amr',
        '-i',str(source),'-map','0:a:0','-vn','-ac','1','-ar','16000','-t','14401',
        '-f','wav','-y',str(output)],capture_output=True,timeout=240)
    if result.returncode:
        raise ValueError('无法读取这份音频。请先用系统导出或转换成 WAV / MP3 后重新导入；原文件已保留。')
    with wave.open(str(output),'rb') as audio:
        seconds=audio.getnframes()/audio.getframerate()
    if not 0<seconds<=14400:
        raise ValueError('单次录音须大于 0 秒且不超过 4 小时，原文件已保留。')
    return seconds


DEFAULT_API_BASE='https://api.siliconflow.cn/v1'


def api_base():
    """Any OpenAI-compatible endpoint that serves /audio/transcriptions and /chat/completions."""
    return os.environ.get('SCRIBELET_API_BASE','').strip().rstrip('/') or DEFAULT_API_BASE


def api_key():
    return (os.environ.get('SCRIBELET_API_KEY') or os.environ.get('SILICONFLOW_API_KEY') or '').strip()


def asr_model():
    return os.environ.get('SCRIBELET_ASR_MODEL','').strip() or 'FunAudioLLM/SenseVoiceSmall'


def note_model():
    return os.environ.get('SCRIBELET_MODEL','').strip() or 'Qwen/Qwen3.5-35B-A3B'


def extra_body():
    """Provider-specific request fields. SiliconFlow's Qwen needs thinking switched off;
    other providers reject unknown fields, so nothing is sent unless asked for."""
    raw=os.environ.get('SCRIBELET_EXTRA_BODY')
    if raw is None:
        return {'enable_thinking':False} if api_base()==DEFAULT_API_BASE else {}
    value=json.loads(raw) if raw.strip() else {}
    if not isinstance(value,dict):
        raise ValueError('SCRIBELET_EXTRA_BODY 必须是 JSON 对象。')
    return value


class Provider:
    def __init__(self, store):
        self.store=store
        key=api_key()
        if not key:
            raise ValueError('尚未配置模型服务密钥，录音已保留。')
        self.client=httpx.Client(base_url=api_base(),headers={'Authorization':'Bearer '+key},
                                 timeout=httpx.Timeout(300,connect=20),follow_redirects=False)

    def close(self):
        self.client.close()

    def check(self, response):
        if response.status_code!=200:
            raise ValueError({401:'模型服务密钥无效',402:'模型账户余额不足',429:'模型服务暂时限流'}.get(response.status_code,'模型服务暂时不可用')+'；录音已保存，可稍后重试。')
        return response.json()

    def transcribe(self, path, seconds):
        self.store.reserve(audio=seconds)
        with path.open('rb') as audio:
            response=self.client.post('/audio/transcriptions',data={'model':asr_model()},files={'file':('segment.wav',audio,'audio/wav')})
        result=self.check(response).get('text','')
        if not isinstance(result,str):
            raise ValueError('转写返回格式异常，原音频已保留。')
        return clean_transcript(result)

    def summarize(self, prompt):
        # UTF-8 bytes upper-bound input tokens conservatively. Reserve even on timeout:
        # a timed-out upstream call may still have incurred cost. No automatic retries.
        reserved=len(prompt.encode('utf-8'))+8192+256
        day=self.store.reserve(tokens=reserved)
        response=self.client.post('/chat/completions',json={
            **extra_body(),
            'model':note_model(),
            'messages':[{'role':'user','content':prompt}], 'temperature':0.1,
            'max_tokens':8192})
        body=self.check(response)
        # The byte count over-reserves ~3x; keep only what the provider says it billed.
        usage=body.get('usage') if isinstance(body,dict) else None
        used=usage.get('total_tokens') if isinstance(usage,dict) else None
        if type(used) is int and 0<=used<reserved:
            self.store.release_tokens(day,reserved-used)
        choices=body.get('choices') if isinstance(body,dict) else None
        if not isinstance(choices,list) or not choices or not isinstance(choices[0],dict):
            raise ValueError('笔记返回格式异常，转写和音频已保留，可重试。')
        choice=choices[0]
        message=choice.get('message')
        text=message.get('content','') if isinstance(message,dict) else None
        if choice.get('finish_reason')=='length' or not isinstance(text,str) or not text.strip():
            raise ValueError('笔记输出不完整，转写和音频已保留，可重试。')
        return text.strip()


NO_SPEECH='[No speech detected / 未识别到语音]'
# SenseVoice "rich transcription" appends emotion/event marks (😊, 👏, <|HAPPY|>…).
# They are metadata, not words; keep them out of the archived text.
_MARKS=re.compile(r'<\|[^|]*\|>|[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]')


def clean_transcript(text):
    return re.sub(r'\s{2,}',' ',_MARKS.sub('',text)).strip()


def detect_silences(path, noise_db=-35, minimum=0.35):
    """[(start, end), ...] of silences via ffmpeg silencedetect; [] if unavailable."""
    try:
        result=subprocess.run([ffmpeg(),'-nostdin','-v','info','-i',str(path),
                               '-af','silencedetect=noise=%ddB:d=%s'%(noise_db,minimum),'-f','null','-'],
                              capture_output=True,text=True,timeout=600)
    except Exception:
        return []
    starts=[float(m) for m in re.findall(r'silence_start: ([\d.]+)',result.stderr)]
    ends=[float(m) for m in re.findall(r'silence_end: ([\d.]+)',result.stderr)]
    return list(zip(starts,ends))


def plan_segments(total, silences, target=45.0, window=8.0, maximum=55.0, minimum=6.0):
    """Cut near every `target` seconds, but inside a pause when one is within `window`.

    A cut mid-sentence costs the recogniser the context on both sides; a cut inside
    a pause costs nothing. Deterministic for the same audio, so retries and
    restarts land on the same segments."""
    bounds=[]; start=0.0
    while total-start>maximum:
        wish=start+target
        best=None
        for s0,s1 in silences:
            if s0+0.15>s1-0.15:
                continue
            point=min(max(wish,s0+0.15),s1-0.15)          # closest point inside the pause
            if point<=start+minimum or abs(point-wish)>window:
                continue
            if best is None or abs(point-wish)<abs(best-wish):
                best=point
        cut=best if best is not None else wish
        bounds.append((start,cut)); start=cut
    bounds.append((start,total))
    return bounds


def parse_translations(answer, wanted):
    """Accept wrapped lines and full-width delimiters; duplicates are ambiguous.

    Unnumbered lines before the first ID are a preamble. Continuations after an
    ID belong to that segment; a repeated/unknown ID is never called complete.
    """
    parsed={}; duplicates=set(); current=None; invalid=False
    for line in answer.splitlines():
        m=re.match(r'^\s*[\[［](\d+)[\]］]\s*(.*?)\s*$',line)
        if m:
            current=int(m.group(1))
            if current not in wanted:
                invalid=True;current=None;continue
            if current in parsed:
                duplicates.add(current);invalid=True
            parsed.setdefault(current,[]).append(m.group(2))
        elif current is not None and line.strip():
            parsed[current].append(line.strip())
    return {k:'\n'.join(v).strip() for k,v in parsed.items()
            if k not in duplicates and '\n'.join(v).strip()},invalid


class CheckpointProvider:
    """Per-record successful calls survive a later quota failure or restart."""
    def __init__(self, provider, store, rid):
        self.provider,self.store,self.rid=provider,store,rid

    def summarize(self,prompt):
        model=note_model()
        key=hashlib.sha256(('checkpoint-v1\0'+model+'\0'+prompt).encode()).hexdigest()
        with self.store.db() as db:
            row=db.execute('SELECT result FROM model_results WHERE record=? AND key=?',(self.rid,key)).fetchone()
        if row:return row[0]
        result=self.provider.summarize(prompt)
        # Do not freeze a malformed translation: regeneration must be able to repair it.
        if '<segments>' in prompt:
            material=prompt.split('<segments>\n',1)[1].rsplit('\n</segments>',1)[0]
            wanted={int(i):True for i in re.findall(r'^\[(\d+)\]',material,re.M)}
            parsed,invalid=parse_translations(result,wanted)
            if invalid or set(parsed)!=set(wanted):return result
        with self.store.db() as db:
            db.execute('INSERT OR REPLACE INTO model_results VALUES(?,?,?)',(self.rid,key,result))
        return result


def summary_batches(spoken,limit=18000):
    """Pack whole timestamped segments; oversized segments start at whitespace.

    Repeat the timestamp on continuation pieces, so each prompt has provenance.
    """
    batch=''
    for segment in spoken:
        if len(segment)>limit:
            mark=re.match(r'^\[\d{2,}:\d{2}\]\s*',segment)
            prefix=mark.group(0) if mark else ''
            remaining=segment[len(prefix):]
            pieces=[]
            while remaining:
                width=limit-len(prefix)
                cut=min(width,len(remaining))
                if cut<len(remaining):
                    space=remaining.rfind(' ',0,cut+1)
                    if space>0:cut=space
                pieces.append(prefix+remaining[:cut]);remaining=remaining[cut:].lstrip()
        else:pieces=[segment]
        for piece in pieces:
            if batch and len(batch)+2+len(piece)>limit:
                yield batch;batch=''
            batch=(batch+'\n\n'+piece) if batch else piece
    if batch:yield batch


def translate_segments(provider, spoken, batch_chars=9000):
    """(Chinese keyed by segment index, reason of the last failed batch or '').

    A failed batch leaves those segments English-only; the caller must say so in the note."""
    numbered=[(idx,re.sub(r'^\[\d\d:\d\d\]\s*','',text)) for idx,text in spoken]
    translations={}
    failure=''
    batch=[]; size=0
    def flush():
        nonlocal failure
        if not batch: return
        prompt=build_translation_prompt('\n'.join('[%d] %s'%(i,t) for i,t in batch))
        try:
            answer=provider.summarize(prompt)
        except ValueError as exc:
            failure=str(exc); return
        except Exception:
            failure='模型服务暂时不可用'; return
        parsed, invalid=parse_translations(answer,dict(batch))
        translations.update(parsed)
        if invalid or len(parsed)!=len(batch):
            failure='翻译返回格式异常或缺少编号'
    for idx,text in numbered:
        if size+len(text)>batch_chars and batch:
            flush(); batch=[]; size=0
        batch.append((idx,text)); size+=len(text)
    flush()
    return translations, failure



def speech_coverage(segments):
    """Share of empty segments and longest empty run; these are counts, not durations."""
    ordered=[NO_SPEECH in segments[k] for k in sorted(segments)]
    longest=run=0
    for empty in ordered:
        run=run+1 if empty else 0
        longest=max(longest,run)
    return {'empty_ratio':(sum(ordered)/len(ordered)) if ordered else 0.0,'longest_gap':longest}


def compose_note(provider, title, done):
    """Summary + bilingual transcript from finished ASR segments {idx: text}."""
    # The marker is a processing state, not something the lecturer said:
    # keep it in the archived transcript, keep it out of the model's input.
    spoken=[done[k] for k in sorted(done) if NO_SPEECH not in done[k]]
    quality=speech_coverage(done)
    if not spoken:
        raise ValueError('没有识别到语音，未调用笔记模型。请下载原音频检查声音。')
    model_input='\n\n'.join(spoken)
    if len(model_input)>300000:
        raise ValueError('转写过长，请拆分录音；全文及音频已保留。')
    if len(model_input)>18000:
        batches=[]
        for material in summary_batches(spoken):
            batches.append(provider.summarize(build_summary_prompt(material,'classic')))
        prompt=build_staged_summary_prompt('\n\n'.join(batches),'classic')
    else:
        prompt=build_summary_prompt(model_input,'classic')
    result=provider.summarize(prompt)
    caution=''
    if quality['empty_ratio']>0.2 or quality['longest_gap']>=3:
        caution=('> ⚠ 录音质量可能有问题：%d%% 的分段未识别到语音，最长连续 %d 段；分段长度不同，此比例不是静音时长比例。'
                 '笔记只基于识别到的部分，请对照原音频。\n\n'
                 % (round(quality['empty_ratio']*100),quality['longest_gap']))
    # Bilingual transcript: each English segment followed by its Chinese.
    pairs=[(k,done[k]) for k in sorted(done) if NO_SPEECH not in done[k]]
    chinese,failure=translate_segments(provider,pairs)
    missing=sum(1 for k,_ in pairs if k not in chinese)
    if missing:
        caution+=('> ⚠ 原始转写有 %d / %d 段没有生成中文对照%s；原始转写已保留，可点“重新生成笔记”补全。\n\n'
                  % (missing,len(pairs),'（'+failure.removesuffix('；录音已保存，可稍后重试。').rstrip('。')+'）' if failure else ''))
    lines=[]
    for k in sorted(done):
        lines.append(done[k])
        if k in chinese:
            lines.append(chinese[k])
    heading='## Transcript 原始转写（中英对照）' if chinese else '## Transcript 原始转写'
    return '# '+title+'\n\n'+caution+result+'\n\n'+heading+'\n\n'+'\n\n'.join(lines)


def save_note(store,rid,note):
    from app import MAX_REVISIONS
    with store.db() as db:
        previous=db.execute('SELECT * FROM records WHERE id=?',(rid,)).fetchone()
        db.execute('INSERT OR IGNORE INTO revisions VALUES(?,?,?,?,?)',(rid,previous['version'],previous['title'],previous['note'],time.time()))
        db.execute('DELETE FROM revisions WHERE record=? AND version NOT IN '
                   '(SELECT version FROM revisions WHERE record=? ORDER BY version DESC LIMIT ?)',
                   (rid,rid,MAX_REVISIONS))
        db.execute("UPDATE records SET note=?,state='done',version=version+1,progress=100,error='',rebuild_note=0,updated=? WHERE id=?",(note,time.time(),rid))


def process_record(store, rid, provider_factory=Provider, converter=convert):
    from app import MAX_REVISIONS
    record=store.record(rid)
    if record['rebuild_note']:
        store.reserve(jobs=1)
        provider=provider_factory(store)
        try:
            with store.db() as db:
                done={r['idx']:r['text'] for r in db.execute('SELECT idx,text FROM segments WHERE record=?',(rid,))}
            note=compose_note(CheckpointProvider(provider,store,rid),record['title'],done)
            save_note(store,rid,note)
        finally:
            provider.close()
        return
    expected=math.ceil(record['size']/(2*1024*1024))
    with store.db() as db:
        count,total=db.execute('SELECT COUNT(*),COALESCE(SUM(LENGTH(data)),0) FROM parts WHERE record=?',(rid,)).fetchone()
    if count!=expected or total!=record['size']:
        raise ValueError('上传尚未完成')
    # Reserve an attempt before contacting any billable endpoint.
    store.reserve(jobs=1)
    with tempfile.TemporaryDirectory(dir=store.root,prefix='job_') as directory:
        folder=Path(directory)
        source, normalized=folder/'input.media',folder/'audio.wav'
        with source.open('wb') as output:
            for idx in range(expected):
                with store.db() as db:
                    part=db.execute('SELECT data FROM parts WHERE record=? AND idx=?',(rid,idx)).fetchone()
                if not part:
                    raise ValueError('上传尚未完成')
                output.write(part[0])
        duration=converter(source,normalized)
        provider=provider_factory(store)
        try:
            with store.db() as db:
                done={r['idx']:r['text'] for r in db.execute('SELECT idx,text FROM segments WHERE record=?',(rid,))}
                db.execute('UPDATE records SET duration=? WHERE id=?',(duration,rid))
            with wave.open(str(normalized),'rb') as audio:
                rate=audio.getframerate()
                total=audio.getnframes()/rate
                with store.db() as db:
                    saved=db.execute('SELECT plan FROM segment_plans WHERE record=?',(rid,)).fetchone()
                if saved:
                    plan=json.loads(saved[0])
                    # Cut points are absolute seconds; tolerate resampler drift across ffmpeg versions.
                    if abs(plan[-1][1]-total)>0.5:
                        raise ValueError('音频转换时长变化，不能安全复用转写切分；请检查原音频。')
                else:
                    if done:
                        raise ValueError('旧转写没有切分计划，不能安全续跑；请导出转写或重新导入音频。')
                    plan=plan_segments(total,detect_silences(normalized))
                    with store.db() as db:
                        db.execute('INSERT INTO segment_plans VALUES(?,?)',(rid,json.dumps(plan)))
                count=len(plan)
                for idx,(begin,end) in enumerate(plan):
                    if idx in done:
                        continue
                    audio.setpos(int(begin*rate))
                    data=audio.readframes(int((end-begin)*rate))
                    path=folder/'segment.wav'
                    with wave.open(str(path),'wb') as segment:
                        segment.setparams(audio.getparams())
                        segment.writeframes(data)
                    start=int(begin)
                    seconds=len(data)/(rate*audio.getsampwidth()*audio.getnchannels())
                    text=provider.transcribe(path,seconds)
                    value=f'[{start//60:02d}:{start%60:02d}] '+(text or NO_SPEECH)
                    done[idx]=value
                    with store.db() as db:
                        db.execute('INSERT OR REPLACE INTO segments VALUES(?,?,?)',(rid,idx,value))
                        db.execute('UPDATE records SET progress=?,updated=? WHERE id=?',(int((idx+1)/count*80),time.time(),rid))
            note=compose_note(CheckpointProvider(provider,store,rid),record['title'],done)
            save_note(store,rid,note)
        finally:
            provider.close()


def run_worker(store, stopped):
    with store.db() as db:
        db.execute("UPDATE records SET state='error',error='服务重启中断了任务，录音和已完成转写仍在，请点击重试。' WHERE state='processing'")
    while not stopped.is_set():
        with store.db() as db:
            row=db.execute("SELECT id FROM records WHERE state='queued' AND deleted=0 ORDER BY created LIMIT 1").fetchone()
            if row:
                db.execute("UPDATE records SET state='processing',updated=? WHERE id=?",(time.time(),row[0]))
        if not row:
            stopped.wait(1)
            continue
        try:
            process_record(store,row[0])
        except Exception as exc:
            # Never persist raw HTTP exceptions, request headers, credentials or tracebacks.
            if isinstance(exc,httpx.TimeoutException):
                message='模型服务响应超时，音频和已完成转写已保存。可稍后重试；不会自动重复调用。'
            elif isinstance(exc,httpx.ConnectError):
                message='暂时无法连接模型服务，音频和已完成转写已保存，请稍后重试。'
            else:
                message=str(exc) if isinstance(exc,ValueError) else '处理失败，录音和已完成转写已保存，请稍后重试。'
            with store.db() as db:
                db.execute("UPDATE records SET state='error',error=?,updated=? WHERE id=?",(message[:400],time.time(),row[0]))
