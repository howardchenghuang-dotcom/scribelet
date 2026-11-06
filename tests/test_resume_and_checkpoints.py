"""Synthetic regression contracts. Set SCRIBELET_APP_ROOT to the tree under test."""
import concurrent.futures
from datetime import datetime,timezone
import json,os,re,sys,tempfile,wave
from pathlib import Path
from unittest.mock import patch
import pytest,httpx
ROOT=Path(os.environ.get('SCRIBELET_APP_ROOT',Path(__file__).resolve().parents[1])).resolve()
sys.path.insert(0,str(ROOT))
import app,processing as p
from app import Store
from bilingual import build_summary_prompt

@pytest.fixture
def store(tmp_path):return Store(tmp_path/'synthetic-store')

def provider(store,body):
 with patch.dict(os.environ,{'SILICONFLOW_API_KEY':'test-only'}):v=p.Provider(store)
 v.client.close()
 v.client=httpx.Client(base_url='https://mock.invalid',transport=httpx.MockTransport(lambda req:httpx.Response(200,json=body)))
 return v

def tokens(s):
 with s.db() as db:return db.execute('SELECT COALESCE(SUM(tokens),0) FROM usage').fetchone()[0]

class Clock:
 value=datetime(2026,10,22,23,59,59,tzinfo=timezone.utc)
 @classmethod
 def now(cls,tz):return cls.value

def test_refund_original_utc_day(store):
 with patch.object(app,'datetime',Clock):
  Clock.value=datetime(2026,10,22,23,59,59,tzinfo=timezone.utc);day=store.reserve(tokens=90)
  Clock.value=datetime(2026,10,23,0,0,1,tzinfo=timezone.utc);store.reserve(tokens=80)
  store.release_tokens(day,70)
 with store.db() as db:rows=dict(db.execute('SELECT day,tokens FROM usage'))
 assert rows=={'2026-10-22':20,'2026-10-23':80},rows

def test_concurrent_settlement(store):
 with patch.dict(os.environ,{'SCRIBELET_DAILY_TOKENS':'1000'}):
  d=store.reserve(tokens=600)
  def attempt(_):
   try:store.reserve(tokens=100);return True
   except ValueError:return False
  with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
   futures=[pool.submit(attempt,i) for i in range(8)]
   store.release_tokens(d,400)
   accepted=sum(f.result() for f in futures)
 assert tokens(store)==200+100*accepted and tokens(store)<=1000
 # a second wave must still enforce the same cap
 with patch.dict(os.environ,{'SCRIBELET_DAILY_TOKENS':'1000'}):
  with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:more=sum(pool.map(attempt,range(8)))
 assert tokens(store)==200+100*(accepted+more)<=1000

def test_usage_shape_does_not_destroy_valid_output(store):
 body={'choices':[{'finish_reason':'stop','message':{'content':'Complete 完整'}}],'usage':['invalid']}
 v=provider(store,body)
 try:assert v.summarize('hello')=='Complete 完整'
 finally:v.close()
 assert tokens(store)==len(b'hello')+8448

def test_incomplete_output_still_settles_usage(store):
 for reason,text in [('length','partial'),('stop','')]:
  before=tokens(store);v=provider(store,{'choices':[{'finish_reason':reason,'message':{'content':text}}],'usage':{'total_tokens':321}})
  try:
   with pytest.raises(ValueError,match='不完整'):v.summarize('hello')
  finally:v.close()
  assert tokens(store)-before==321

class Fake:
 def __init__(self,store):self.store=store
 def close(self):pass
 def transcribe(self,path,seconds,language=None):
  with wave.open(str(path)) as a:values=a.readframes(a.getnframes())
  # source encodes seconds as sample values; gaps/overlaps visible in text
  import struct
  frames=struct.unpack('<'+'h'*(len(values)//2),values)
  return f'SAMPLES {frames[0]}..{frames[-1]}'
 def summarize(self,prompt):
  if '<segments>' in prompt:return '\n'.join('[%s] 译文'%i for i in re.findall(r'^\[(\d+)\]',prompt,re.M))
  return 'Summary 摘要'

def seed(store):
 rid='a'*32
 with store.db() as db:
  db.execute("INSERT INTO records(id,title,size,mime,state,created,updated) VALUES(?,?,1,'audio/wav','error',1,1)",(rid,'Synthetic'))
  db.execute('INSERT INTO parts VALUES(?,0,?,?)',(rid,'fake',b'x'))
 return rid

def converter(src,out):
 import struct
 with wave.open(str(out),'wb') as a:
  a.setnchannels(1);a.setsampwidth(2);a.setframerate(10)
  a.writeframes(struct.pack('<'+'h'*1200,*[i//10 for i in range(1200)]))
 return 120

def test_resume_freezes_segment_plan(store):
 rid=seed(store)
 class Fail(Fake):
  n=0
  def transcribe(self,*args):
   Fail.n+=1
   if Fail.n==2:raise ValueError('synthetic interruption')
   return super().transcribe(*args)
 with patch.object(p,'detect_silences',return_value=[(36,38)]):
  with pytest.raises(ValueError):p.process_record(store,rid,Fail,converter)
 with patch.object(p,'detect_silences',return_value=[]):p.process_record(store,rid,Fake,converter)
 note=store.record(rid)['note']
 assert 'SAMPLES 0..37' in note and 'SAMPLES 37..82' in note and 'SAMPLES 82..119' in note,note

def test_legacy_cache_is_not_silently_reinterpreted(store):
 rid=seed(store)
 with store.db() as db:db.execute('INSERT INTO segments VALUES(?,0,?)',(rid,'[00:00] unknown old boundaries'))
 with patch.object(p,'detect_silences',return_value=[]):
  with pytest.raises(ValueError,match='旧转写.*切分'):p.process_record(store,rid,Fake,converter)
 assert store.record(rid)['state']!='done'

def test_summary_batches_preserve_timestamp_units():
 prompts=[]
 class Model(Fake):
  def summarize(self,prompt):prompts.append(prompt);return super().summarize(prompt)
 done={0:'[00:00] '+'x'*17987,1:'[45:00] Last key fact.'}
 p.compose_note(Model(None),'Synthetic',done)
 material=[x.split('<course_material>\n')[1].split('\n</course_material>')[0] for x in prompts if '<course_material>' in x]
 assert any('[45:00] Last key fact.' in x for x in material),material[-2:]
 assert not any(x.endswith('[45') for x in material)

def test_multiline_and_fullwidth_translations_are_complete():
 class Model:
  def summarize(self,prompt):return '好的，以下是翻译\n[0] 第一行。\n第二行关键结论。\n［1］第二段。'
 result,failure=p.translate_segments(Model(),[(0,'[00:00] First'),(1,'[00:45] Second')])
 assert result=={0:'第一行。\n第二行关键结论。',1:'第二段。'},result
 assert failure==''

def test_ambiguous_translation_is_not_counted_complete():
 class Model:
  def summarize(self,prompt):return '[0] 第一次。\n[0] 冲突版本。'
 result,failure=p.translate_segments(Model(),[(0,'[00:00] First')])
 assert result=={} and failure,result

def test_summary_checkpoint_survives_quota_retry(store):
 rid=seed(store)
 class Model(Fake):
  calls=[];blocked=True
  def transcribe(self,*args):return 'x'*10000
  def summarize(self,prompt):
   Model.calls.append(prompt)
   if Model.blocked and len(Model.calls)==2:raise ValueError('今日用量已达上限')
   return super().summarize(prompt)
 with patch.object(p,'detect_silences',return_value=[]):
  with pytest.raises(ValueError):p.process_record(store,rid,Model,converter)
  first=Model.calls[0];Model.blocked=False
  p.process_record(store,rid,Model,converter)
 assert Model.calls.count(first)==1,len(Model.calls)
 assert store.record(rid)['state']=='done'

def test_quality_warning_describes_segments_not_duration():
 class Model(Fake):pass
 note=p.compose_note(Model(None),'Synthetic',{0:'[00:00] '+p.NO_SPEECH,1:'[00:37] words',2:'[01:30] '+p.NO_SPEECH})
 assert '67% 的分段' in note and '45 秒' not in note and '67% 的时段' not in note,note

def test_fractional_audio_does_not_round_every_segment(store,tmp_path):
 audio=tmp_path/'synthetic.wav';audio.write_bytes(b'fake')
 with patch.dict(os.environ,{'SILICONFLOW_API_KEY':'test-only'}):v=p.Provider(store)
 v.client.close();v.client=httpx.Client(base_url='https://mock.invalid',transport=httpx.MockTransport(lambda req:httpx.Response(200,json={'text':'word'})))
 try:
  v.transcribe(audio,37.4);v.transcribe(audio,52.6)
 finally:v.close()
 with store.db() as db:assert db.execute('SELECT audio FROM usage').fetchone()[0]==90


def test_amr_demuxer_and_actionable_error(tmp_path):
 import subprocess
 audio=tmp_path/'synthetic.amr';audio.write_bytes(b'#!AMR\n'+bytes.fromhex('3c')+b'\0'*31)
 with patch.object(p.subprocess,'run',return_value=subprocess.CompletedProcess([],1,b'',b'')) as run:
  with pytest.raises(ValueError,match='转.*WAV|转换.*MP3'):p.convert(audio,tmp_path/'out.wav')
 assert 'amr' in run.call_args.args[0][run.call_args.args[0].index('-format_whitelist')+1].split(',')

def test_completed_note_can_rebuild_without_asr(store):
 from fastapi.testclient import TestClient
 service=app.create_app(store.root,'https://testserver','owner',app.password_hash('synthetic-password'),start_worker=False)
 rid=seed(store)
 with store.db() as db:
  db.execute("UPDATE records SET state='done',note='Original note' WHERE id=?",(rid,))
  db.execute('INSERT INTO segments VALUES(?,0,?)',(rid,'[00:00] words'))
 with TestClient(service,base_url='https://testserver') as client:
  client.post('/api/login',headers={'Origin':'https://testserver'},json={'username':'owner','password':'synthetic-password'})
  h={'Origin':'https://testserver','X-CSRF-Token':client.get('/api/me').json()['csrf']}
  for _ in range(2):
   response=client.post(f'/api/records/{rid}/regenerate',headers=h,json={})
   assert response.status_code==200,response.text
  with patch.object(p,'convert',side_effect=AssertionError('no new audio conversion')):
   p.process_record(store,rid,Fake,lambda *args:(_ for _ in ()).throw(AssertionError('no ASR')))
 assert store.record(rid)['state']=='done'
 with store.db() as db:
  assert db.execute('SELECT note FROM revisions WHERE record=?',(rid,)).fetchone()[0]=='Original note'
  assert db.execute('SELECT SUM(audio) FROM usage').fetchone()[0]==0

def test_automatic_submit_receipt_survives_worker_error(store):
 from fastapi.testclient import TestClient
 service=app.create_app(store.root,'https://testserver','owner',app.password_hash('synthetic-password'),start_worker=False)
 rid=seed(store)
 with TestClient(service,base_url='https://testserver') as client:
  client.post('/api/login',headers={'Origin':'https://testserver'},json={'username':'owner','password':'synthetic-password'})
  h={'Origin':'https://testserver','X-CSRF-Token':client.get('/api/me').json()['csrf']}
  path=f'/api/records/{rid}/submit';body={'submission_id':rid}
  assert client.post(path,headers=h,json=body).json()['state']=='queued'
  # First submit response was lost; worker has since failed after spending quota.
  with store.db() as db:db.execute("UPDATE records SET state='error',error='模型超时' WHERE id=?",(rid,))
  assert client.post(path,headers=h,json=body).json()['state']=='error'
  assert store.record(rid)['state']=='error'
  # An explicit user retry has a new intent (existing UI sends {}).
  assert client.post(path,headers=h,json={}).json()['state']=='queued'

def test_chinese_4h_can_resume_next_day_without_rebilling_finished_prompts(store):
 from collections import Counter
 done={i:f'[{i*45//60:02d}:{i*45%60:02d}] '+('中文课程涉及定义条件公式因果关系以及课堂案例。'*40)[:800] for i in range(320)}
 prompts=[]
 def transport(request):
  prompt=json.loads(request.content)['messages'][0]['content'];prompts.append(prompt)
  text=('Concept 概念 — explanation 解释。'*400)[:8000]
  if '<segments>' in prompt:text='\n'.join('[%s] 中文译文'%i for i in re.findall(r'^\[(\d+)\]',prompt,re.M))
  return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':text}}]})
 with patch.dict(os.environ,{'SILICONFLOW_API_KEY':'test-only','SCRIBELET_DAILY_TOKENS':'1200000'}),patch.object(app,'datetime',Clock):
  v=p.Provider(store);v.client.close();v.client=httpx.Client(base_url='https://mock.invalid',transport=httpx.MockTransport(transport))
  # Baseline has no checkpoint layer: exercise its real compose path, not an import error.
  wrapped=getattr(p,'CheckpointProvider',lambda provider,*args:provider)(v,store,'a'*32)
  try:
   Clock.value=datetime(2026,10,22,12,tzinfo=timezone.utc)
   first=p.compose_note(wrapped,'Synthetic Chinese',done)
   assert '没有生成中文对照' in first
   Clock.value=datetime(2026,10,23,12,tzinfo=timezone.utc)
   second=p.compose_note(wrapped,'Synthetic Chinese',done)
   assert '没有生成中文对照' not in second
  finally:v.close()
 with store.db() as db:assert all(r[0]<=1200000 for r in db.execute('SELECT tokens FROM usage'))
 assert max(Counter(prompts).values())==1,'already successful prompts must not incur another request'

def test_resume_tolerates_small_resampler_drift(store):
 rid=seed(store)
 class Fail(Fake):
  n=0
  def transcribe(self,*args):
   Fail.n+=1
   if Fail.n==2:raise ValueError('synthetic interruption')
   return super().transcribe(*args)
 def drifted(src,out):
  import struct
  with wave.open(str(out),'wb') as a:
   a.setnchannels(1);a.setsampwidth(2);a.setframerate(10)
   a.writeframes(struct.pack('<'+'h'*1202,*[i//10 for i in range(1202)]))
  return 120.2
 with patch.object(p,'detect_silences',return_value=[]):
  with pytest.raises(ValueError):p.process_record(store,rid,Fail,converter)
  p.process_record(store,rid,Fake,drifted)
 assert store.record(rid)['state']=='done'

def test_library_exposes_size_so_text_notes_hide_regenerate(store):
 from fastapi.testclient import TestClient
 service=app.create_app(store.root,'https://testserver','owner',app.password_hash('synthetic-password'),start_worker=False)
 with TestClient(service,base_url='https://testserver') as client:
  client.post('/api/login',headers={'Origin':'https://testserver'},json={'username':'owner','password':'synthetic-password'})
  h={'Origin':'https://testserver','X-CSRF-Token':client.get('/api/me').json()['csrf']}
  assert client.post('/api/import-note',headers=h,json={'id':'b'*32,'title':'Old','note':'# Old'}).status_code==200
  assert client.get('/api/records').json()[0]['size']==0
