import concurrent.futures
import io
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import wave

from fastapi.testclient import TestClient
import httpx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import create_app, password_hash, PART_SIZE, Store
from processing import process_record, Provider, convert, run_worker

ORIGIN='https://testserver'
PASSWORD='a-test-password-only-1234'
HASH=password_hash(PASSWORD)
RID='a'*32

def test_ffmpeg():
    configured=os.environ.get('SCRIBELET_FFMPEG') or shutil.which('ffmpeg')
    if configured:
        return configured
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()

class PrivateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.app=create_app(self.temp.name,ORIGIN,'owner',HASH,start_worker=False)
        self.client=TestClient(self.app,base_url=ORIGIN)
        self.store=self.app.state.store

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def login(self, client=None):
        client=client or self.client
        response=client.post('/api/login',headers={'Origin':ORIGIN},json={'username':'owner','password':PASSWORD})
        self.assertEqual(response.status_code,200,response.text)
        csrf=client.get('/api/me').json()['csrf']
        return {'Origin':ORIGIN,'X-CSRF-Token':csrf}

    def create(self, data=b'audio', rid=RID):
        headers=self.login()
        response=self.client.post('/api/records',headers=headers,json={'id':rid,'title':'Ethics 伦理','size':len(data),'mime':'audio/wav'})
        self.assertEqual(response.status_code,200,response.text)
        for i in range(0,len(data),PART_SIZE):
            response=self.client.put(f'/api/records/{rid}/parts/{i//PART_SIZE}',headers=headers,content=data[i:i+PART_SIZE])
            self.assertEqual(response.status_code,200,response.text)
        return headers

    def test_all_private_endpoints_reject_anonymous_without_side_effects(self):
        routes=[('POST','/api/import-note'),('GET','/api/me'),('GET','/api/records'),('POST','/api/records'),('GET',f'/api/records/{RID}'),
            ('PUT',f'/api/records/{RID}/parts/0'),('POST',f'/api/records/{RID}/submit'),('POST',f'/api/records/{RID}/edit'),
            ('POST',f'/api/records/{RID}/trash'),('GET',f'/api/records/{RID}/audio'),('POST','/api/revoke-others'),('GET','/private.env')]
        for method,path in routes:
            self.assertEqual(self.client.request(method,path,headers={'Origin':ORIGIN},json={}).status_code,401,path)
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM records').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM usage').fetchone()[0],0)

    def test_login_cookie_csrf_origin_and_no_public_registration(self):
        self.assertEqual(self.client.post('/api/login',json={}).status_code,403)
        self.assertEqual(self.client.post('/api/login',headers={'Origin':ORIGIN},json={'username':'other','password':PASSWORD}).status_code,401)
        headers=self.login()
        cookie=next(c for c in self.client.cookies.jar if c.name=='__Host-scribelet')
        self.assertTrue(cookie.secure)
        self.assertEqual(cookie.path,'/')
        self.assertEqual(self.client.post('/api/revoke-others',headers={'Origin':ORIGIN},json={}).status_code,403)
        self.assertEqual(self.client.post('/api/revoke-others',headers={**headers,'Origin':'https://evil.example'},json={}).status_code,403)
        self.assertEqual(self.client.get('/api/me',headers={'Host':'evil.example'}).status_code,400)
        self.assertIn(self.client.post('/api/register',headers=headers,json={}).status_code,(404,405))
        self.assertIn("frame-ancestors 'none'",self.client.get('/').headers['content-security-policy'])

    def test_revocation_expiry_password_rotation_and_logout(self):
        first=self.login()
        with TestClient(self.app,base_url=ORIGIN) as other:
            second=self.login(other)
            self.assertEqual(other.get('/api/me').status_code,200)
            self.client.post('/api/revoke-others',headers=first,json={})
            self.assertEqual(other.get('/api/me').status_code,401)
        self.assertEqual(self.client.get('/api/me').status_code,200)
        rotated=create_app(self.temp.name,ORIGIN,'owner',password_hash('different-password-12345'),start_worker=False)
        self.assertEqual(self.client.get('/api/me').status_code,401)
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0],0)

    def test_login_rate_limit_persists_across_app_restarts(self):
        with self.store.db() as db:
            db.execute('INSERT INTO attempts VALUES(?,30)',(int(time.time()//900),))
        restarted=create_app(self.temp.name,ORIGIN,'owner',HASH,start_worker=False)
        with TestClient(restarted,base_url=ORIGIN) as client:
            self.assertEqual(client.post('/api/login',headers={'Origin':ORIGIN},json={'username':'owner','password':PASSWORD}).status_code,429)

    def test_resume_idempotency_and_conflicting_part(self):
        data=b'x'*PART_SIZE+b'last'
        headers=self.create(data)
        response=self.client.put(f'/api/records/{RID}/parts/1',headers=headers,content=b'last')
        self.assertEqual(response.status_code,200)
        self.assertEqual(self.client.put(f'/api/records/{RID}/parts/1',headers=headers,content=b'evil').status_code,409)
        self.assertEqual(self.client.get(f'/api/records/{RID}').json()['parts'],[0,1])
        for _ in range(3):
            self.assertEqual(self.client.post(f'/api/records/{RID}/submit',headers=headers,json={}).json()['state'],'queued')
        self.assertEqual(self.client.put(f'/api/records/{RID}/parts/1',headers=headers,content=b'last').status_code,409)
        self.assertEqual(self.client.get(f'/api/records/{RID}/audio').content,data)
        self.assertEqual(self.client.post(f'/api/records/{RID}/trash',headers=headers,json={'deleted':True}).status_code,409)

    def test_partial_upload_cannot_submit(self):
        headers=self.login()
        self.client.post('/api/records',headers=headers,json={'id':RID,'title':'test','size':20,'mime':'audio/wav'})
        self.assertEqual(self.client.put(f'/api/records/{RID}/parts/0',headers=headers,content=b'123').status_code,400)
        self.assertEqual(self.client.post(f'/api/records/{RID}/submit',headers=headers,json={}).status_code,409)

    def test_edit_conflicts_revisions_and_restore(self):
        headers=self.create()
        body={'title':'Changed 改标题','note':'# Changed\nBilingual 双语','version':0}
        self.assertEqual(self.client.post(f'/api/records/{RID}/edit',headers=headers,json=body).status_code,200)
        self.assertEqual(self.client.post(f'/api/records/{RID}/edit',headers=headers,json={**body,'title':'Stale'}).status_code,409)
        document=self.client.get(f'/api/records/{RID}').json()
        self.assertEqual(document['title'],'Changed 改标题')
        self.assertEqual(document['note'],body['note'])
        for deleted in (True,False):
            self.assertEqual(self.client.post(f'/api/records/{RID}/trash',headers=headers,json={'deleted':deleted}).status_code,200)
            self.assertEqual(bool(self.client.get(f'/api/records/{RID}').json()['deleted']),deleted)
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM revisions').fetchone()[0],1)

    def test_concurrent_quota_cannot_overspend(self):
        def reserve(_):
            try:self.store.reserve(tokens=60);return True
            except ValueError:return False
        with patch.dict(os.environ,{'SCRIBELET_DAILY_TOKENS':'100'}):
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                self.assertEqual(sum(pool.map(reserve,range(8))),1)

    def test_api_key_never_in_browser_responses(self):
        headers=self.login()
        with patch.dict(os.environ,{'SILICONFLOW_API_KEY':'SECRET_SENTINEL_NOT_FOR_BROWSER'}):
            for path in ['/','/app.js','/api/me','/api/records','/api/health']:
                self.assertNotIn('SECRET_SENTINEL_NOT_FOR_BROWSER',self.client.get(path).text)
            self.assertEqual(self.client.get('/private.env').status_code,404)

    def test_restart_retains_audio_and_marks_interrupted(self):
        self.create()
        with self.store.db() as db:db.execute("UPDATE records SET state='processing' WHERE id=?",(RID,))
        stopped=threading.Event();stopped.set();run_worker(self.store,stopped)
        self.assertEqual(self.store.record(RID)['state'],'error')
        self.assertEqual(self.client.get(f'/api/records/{RID}/audio').content,b'audio')

    def test_actual_ffmpeg_and_simulated_model_end_to_end(self):
        ffmpeg=test_ffmpeg()
        audio=io.BytesIO()
        with wave.open(audio,'wb') as wav:
            wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(b'\0\0'*32000)
        self.create(audio.getvalue())
        calls=[]
        class Fake:
            def __init__(self,store):pass
            def close(self):pass
            def transcribe(self,path,seconds,language=None):calls.append('asr');return 'Ethics concerns values.'
            def summarize(self,prompt):
                if '<segments>' in prompt:          # 转写翻译
                    calls.append('translate')
                    return '[0] 伦理涉及价值观。'
                calls.append('llm')
                self_test.assertIn('英文在前',prompt)
                return '## Values 价值观\nEthics concerns values. — 伦理涉及价值观。'
        self_test=self
        with patch.dict(os.environ,{'SCRIBELET_FFMPEG':ffmpeg}):
            process_record(self.store,RID,provider_factory=Fake)
        result=self.store.record(RID)
        self.assertEqual(result['state'],'done');self.assertEqual(calls,['asr','llm','translate'])
        self.assertIn('伦理涉及价值观',result['note']);self.assertIn('Transcript 原始转写（中英对照）',result['note'])
        self.assertIn('[00:00] Ethics concerns values.\n\n伦理涉及价值观。',result['note'],'每段英文后面紧跟中文')
        # Retrying a failed summary reuses checkpointed ASR, never loses the source.
        calls.clear()
        with patch.dict(os.environ,{'SCRIBELET_FFMPEG':ffmpeg}):
            process_record(self.store,RID,provider_factory=Fake)
        self.assertEqual(calls,[],'成功的总结和翻译均应复用检查点，不再次付费')

    def test_upstream_payload_and_truncation_no_automatic_retry(self):
        calls=[]
        def transport(request):
            import json
            calls.append(json.loads(request.content))
            return httpx.Response(200,json={'choices':[{'finish_reason':'length','message':{'content':'partial'}}]})
        with patch.dict(os.environ,{'SILICONFLOW_API_KEY':'test-only'}):
            provider=Provider(self.store)
            provider.client.close()
            provider.client=httpx.Client(base_url='https://api.siliconflow.cn/v1',transport=httpx.MockTransport(transport))
            with self.assertRaisesRegex(ValueError,'不完整'):provider.summarize('English 中文')
            provider.close()
        self.assertEqual(len(calls),1)
        self.assertFalse(calls[0]['enable_thinking'])
        with self.store.db() as db:self.assertGreater(db.execute('SELECT tokens FROM usage').fetchone()[0],8192)

    def test_refuse_insecure_public_origin(self):
        with self.assertRaises(ValueError):create_app(self.temp.name,'http://public.example','owner',HASH,start_worker=False)
        with self.assertRaises(ValueError):create_app(self.temp.name,'https://public.example','owner',HASH,start_worker=False,preview=True)

    def test_timeout_keeps_audio_and_reports_retry_without_requeue(self):
        headers=self.create(b'preserved audio')
        self.client.post(f'/api/records/{RID}/submit',headers=headers,json={})
        stopped=threading.Event()
        def timeout(*args):
            stopped.set()
            raise httpx.ReadTimeout('sensitive upstream details must not be persisted')
        with patch('processing.process_record',side_effect=timeout) as attempt:
            run_worker(self.store,stopped)
        doc=self.store.record(RID)
        self.assertEqual(doc['state'],'error')
        self.assertIn('响应超时',doc['error'])
        self.assertNotIn('sensitive',doc['error'])
        self.assertEqual(attempt.call_count,1)
        self.assertEqual(self.client.get(f'/api/records/{RID}/audio').content,b'preserved audio')

    def test_empty_asr_never_spends_on_summary(self):
        ffmpeg=test_ffmpeg()
        audio=io.BytesIO()
        with wave.open(audio,'wb') as wav:
            wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(b'\0\0'*16000)
        self.create(audio.getvalue())
        class Empty:
            def __init__(self,store):pass
            def close(self):pass
            def transcribe(self,*args):return ''
            def summarize(self,*args):raise AssertionError('Silent audio must not reach LLM')
        with patch.dict(os.environ,{'SCRIBELET_FFMPEG':ffmpeg}):
            with self.assertRaisesRegex(ValueError,'没有识别到语音'):
                process_record(self.store,RID,provider_factory=Empty)

    def test_import_existing_notes_without_api_usage(self):
        headers=self.login()
        body={'id':RID,'title':'My old note','note':'# My old note\nEnglish — 中文'}
        for _ in range(2):
            self.assertEqual(self.client.post('/api/import-note',headers=headers,json=body).status_code,200)
        self.assertEqual(self.store.record(RID)['note'],body['note'])
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM records').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM usage').fetchone()[0],0)

    def test_incomplete_audio_download_is_rejected(self):
        headers=self.login()
        size=PART_SIZE+4
        self.client.post('/api/records',headers=headers,json={'id':RID,'title':'Partial','size':size,'mime':'audio/wav'})
        self.client.put(f'/api/records/{RID}/parts/0',headers=headers,content=b'a'*PART_SIZE)
        response=self.client.get(f'/api/records/{RID}/audio')
        self.assertEqual(response.status_code,409)
        self.assertIn('尚未上传完整',response.json()['detail'])

    def test_long_generated_note_remains_editable(self):
        headers=self.create()
        long_note='# Long\n\n'+'English — 中文\n'*12000
        self.assertGreater(len(long_note),150000)
        response=self.client.post(f'/api/records/{RID}/edit',headers=headers,
                                  json={'title':'Long class 长课','note':long_note,'version':0})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.store.record(RID)['note'],long_note)

    def test_purge_requires_trash_and_removes_all_record_data(self):
        headers=self.create(b'complete audio')
        self.assertEqual(self.client.post(f'/api/records/{RID}/purge',headers=headers,json={}).status_code,409)
        with self.store.db() as db:
            db.execute('INSERT INTO segments VALUES(?,?,?)',(RID,0,'transcript'))
            db.execute('INSERT INTO revisions VALUES(?,?,?,?,?)',(RID,0,'old','old note',time.time()))
        self.client.post(f'/api/records/{RID}/trash',headers=headers,json={'deleted':True})
        self.assertEqual(self.client.post(f'/api/records/{RID}/purge',headers=headers,json={}).status_code,200)
        self.assertEqual(self.client.get(f'/api/records/{RID}').status_code,404)
        with self.store.db() as db:
            for table in ('records','parts','segments','revisions'):
                self.assertEqual(db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0],0)

    def test_negative_quota_reservation_is_rejected(self):
        for kwargs in ({'jobs':-1},{'audio':-0.1},{'tokens':float('nan')}):
            with self.assertRaisesRegex(ValueError,'预留值无效'):
                self.store.reserve(**kwargs)
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM usage').fetchone()[0],0)

    def test_titles_reject_newlines_on_create_import_and_edit(self):
        headers=self.login()
        create=self.client.post('/api/records',headers=headers,
            json={'id':RID,'title':'Line one\n# injected','size':1,'mime':'audio/wav'})
        self.assertEqual(create.status_code,400)
        imported=self.client.post('/api/import-note',headers=headers,
            json={'id':RID,'title':'Line one\rLine two','note':'# safe'})
        self.assertEqual(imported.status_code,400)
        headers=self.create()
        edited=self.client.post(f'/api/records/{RID}/edit',headers=headers,
            json={'title':'Bad\nheading','note':'safe','version':0})
        self.assertEqual(edited.status_code,400)

    def test_incomplete_record_does_not_consume_job_quota(self):
        headers=self.login()
        self.client.post('/api/records',headers=headers,
            json={'id':RID,'title':'Partial','size':PART_SIZE+4,'mime':'audio/wav'})
        self.client.put(f'/api/records/{RID}/parts/0',headers=headers,content=b'a'*PART_SIZE)
        class NeverStarted:
            def __init__(self,*args):raise AssertionError('provider must not start')
        with self.assertRaisesRegex(ValueError,'上传尚未完成'):
            process_record(self.store,RID,provider_factory=NeverStarted)
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM usage').fetchone()[0],0)

    def test_root_installer_uses_root_owned_copy_and_current_python(self):
        import mac_finish_setup
        with patch('mac_finish_setup.subprocess.run') as run, patch('mac_finish_setup.secrets.token_hex',return_value='abc123'):
            mac_finish_setup.launch_root_installer()
        commands=[call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[0][:7],['sudo','/usr/bin/install','-o','root','-g','wheel','-m'])
        self.assertEqual(commands[0][7],'0500')
        self.assertEqual(commands[1][0],'sudo')
        self.assertEqual(commands[1][1],str(Path(sys.executable).resolve()))
        self.assertEqual(commands[1][2],commands[0][-1])
        self.assertEqual(commands[1][-1],'--install-system')
        self.assertEqual(commands[2],['sudo','/bin/rm','-f',commands[0][-1]])

    def test_backup_restores_audio_and_notes_but_not_sessions(self):
        from backup import backup
        self.create(b'original-audio')
        destination=Path(self.temp.name)/'backup'/'scribelet.sqlite3'
        backup(self.store.path,destination)
        restored=Store(destination.parent)
        self.assertEqual(restored.record(RID)['title'],'Ethics 伦理')
        with restored.db() as db:
            self.assertEqual(db.execute('SELECT data FROM parts').fetchone()[0],b'original-audio')
            self.assertEqual(db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0],0)
        self.assertTrue(destination.with_suffix('.sha256').exists())

    def test_expired_session_and_explicit_logout(self):
        headers=self.login()
        self.assertEqual(self.client.post('/api/logout',headers=headers,json={}).status_code,200)
        self.assertEqual(self.client.get('/api/me').status_code,401)
        self.login()
        with self.store.db() as db:db.execute('UPDATE sessions SET expires=0')
        self.assertEqual(self.client.get('/api/me').status_code,401)

    # ---- 空正文保护与服务安装回滚 ----

    def test_edit_cannot_silently_clear_an_existing_note(self):
        headers=self.create()
        self.client.post(f'/api/records/{RID}/edit',headers=headers,
                         json={'title':'Ethics 伦理','note':'原始笔记正文','version':0})
        response=self.client.post(f'/api/records/{RID}/edit',headers=headers,
                                  json={'title':'Ethics 伦理','note':'','version':1})
        self.assertEqual(response.status_code,400,response.text)
        self.assertIn('清空整篇笔记',response.json()['detail'])
        self.assertEqual(self.store.record(RID)['note'],'原始笔记正文')

    def test_edit_clears_a_note_only_with_explicit_confirmation(self):
        headers=self.create()
        self.client.post(f'/api/records/{RID}/edit',headers=headers,
                         json={'title':'Ethics 伦理','note':'原始笔记正文','version':0})
        response=self.client.post(f'/api/records/{RID}/edit',headers=headers,
                                  json={'title':'Ethics 伦理','note':'','version':1,'allow_empty':True})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.store.record(RID)['note'],'')

    def test_title_only_edit_still_works_before_any_note_exists(self):
        headers=self.create()
        self.assertFalse(self.store.record(RID)['note'])
        response=self.client.post(f'/api/records/{RID}/edit',headers=headers,
                                  json={'title':'改个标题 renamed','note':'','version':0})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.store.record(RID)['title'],'改个标题 renamed')

    def service_fixture(self):
        import plistlib
        import mac_finish_setup
        root=Path(self.temp.name)/'Scribelet'
        (root/'config').mkdir(parents=True)
        agents=Path(self.temp.name)/'LaunchAgents'
        agents.mkdir()
        daemons=Path(self.temp.name)/'LaunchDaemons'
        daemons.mkdir()
        for label in mac_finish_setup.LABELS:
            body=plistlib.dumps({'Label':label,'ProgramArguments':['/bin/true']})
            (root/'config'/(label+'.plist')).write_bytes(body)
            (agents/(label+'.plist')).write_bytes(body)
        return root,daemons,agents

    def launchd_model(self, loaded=(), fail_bootstrap=None, refuses_to_stop=()):
        """A launchd that remembers what is loaded, so 'did it actually stop?' is real."""
        import subprocess
        state = set(loaded)
        calls = []
        def fake(command, **kwargs):
            calls.append(command)
            if command[:2] == ['launchctl', 'print']:
                return subprocess.CompletedProcess(command, 0 if command[2] in state else 1, '', '')
            if command[:2] == ['launchctl', 'bootout']:
                target = command[2]
                if target in refuses_to_stop:
                    return subprocess.CompletedProcess(command, 1, '', '')
                present = target in state
                state.discard(target)
                return subprocess.CompletedProcess(command, 0 if present else 1, '', '')
            if command[:3] == ['launchctl', 'bootstrap', 'system']:
                label = Path(command[3]).stem
                if fail_bootstrap and label == fail_bootstrap:
                    raise subprocess.CalledProcessError(1, command)
                state.add('system/' + label)
                return subprocess.CompletedProcess(command, 0, '', '')
            if command[:2] == ['launchctl', 'bootstrap']:
                state.add(command[2] + '/' + Path(command[3]).stem)
                return subprocess.CompletedProcess(command, 0, '', '')
            if command[:2] == ['pmset', '-g']:
                return subprocess.CompletedProcess(command, 0, 'AC Power:\n sleep 0\n autorestart 0\n', '')
            return subprocess.CompletedProcess(command, 0, '', '')
        return fake, state, calls

    def test_migration_validates_every_user_agent_before_touching_launchd(self):
        import plistlib
        import pwd
        import mac_finish_setup
        root,daemons,agents=self.service_fixture()
        # Tamper with the LAST label: nothing at all may be migrated.
        (agents/(mac_finish_setup.LABELS[-1]+'.plist')).write_bytes(plistlib.dumps({'Label':'com.attacker.other'}))
        account=pwd.getpwuid(os.getuid())
        with patch('mac_finish_setup.pwd.getpwnam',return_value=account), \
             patch('mac_finish_setup.subprocess.run') as run, patch('mac_finish_setup.os.chown'):
            with self.assertRaises(SystemExit):
                mac_finish_setup.install_system(service_root=root,daemon_dir=daemons,agent_dir=agents)
        self.assertEqual(run.call_args_list,[],'launchd must not be touched before validation completes')
        self.assertEqual(list(daemons.iterdir()),[])
        self.assertEqual(len(list(agents.iterdir())),len(mac_finish_setup.LABELS))

    def test_failed_bootstrap_rolls_back_and_restores_the_user_agent(self):
        import pwd
        import subprocess
        import mac_finish_setup
        root,daemons,agents=self.service_fixture()
        failing=mac_finish_setup.LABELS[1]
        account=pwd.getpwuid(os.getuid())
        fake_run,_,_=self.launchd_model(
            loaded=['gui/%d/%s'%(account.pw_uid,label) for label in mac_finish_setup.LABELS],
            fail_bootstrap=failing)
        with patch('mac_finish_setup.pwd.getpwnam',return_value=account), \
             patch('mac_finish_setup.subprocess.run',side_effect=fake_run) as run, \
             patch('mac_finish_setup.os.chown'):
            with self.assertRaises(SystemExit):
                mac_finish_setup.install_system(service_root=root,daemon_dir=daemons,agent_dir=agents)
        commands=[call.args[0] for call in run.call_args_list]
        self.assertFalse((daemons/(failing+'.plist')).exists(),'half-installed daemon left behind')
        self.assertTrue((agents/(failing+'.plist')).exists(),'user agent must survive a failed migration')
        self.assertIn(['launchctl','bootstrap',f'gui/{account.pw_uid}',str(agents/(failing+'.plist'))],commands)
        self.assertNotIn(['pmset','-a','sleep','0','autorestart','1'],commands)

    def test_power_policy_is_archived_before_it_is_changed(self):
        import pwd
        import subprocess
        import mac_finish_setup
        root,daemons,agents=self.service_fixture()
        account=pwd.getpwuid(os.getuid())
        fake_run,_,order=self.launchd_model(
            loaded=['gui/%d/%s'%(account.pw_uid,label) for label in mac_finish_setup.LABELS])
        def migrate():
            with patch('mac_finish_setup.pwd.getpwnam',return_value=account), \
                 patch('mac_finish_setup.subprocess.run',side_effect=fake_run), \
                 patch('mac_finish_setup.os.chown'):
                mac_finish_setup.install_system(service_root=root,daemon_dir=daemons,agent_dir=agents)
        migrate()
        archive=root/'config'/'pmset-before.txt'
        self.assertIn('autorestart 0',archive.read_text(encoding='utf-8'))
        self.assertLess(order.index(['pmset','-g','custom']),
                        order.index(['pmset','-a','sleep','0','autorestart','1']))
        self.assertEqual(len(list(daemons.iterdir())),len(mac_finish_setup.LABELS))
        self.assertEqual(list(agents.iterdir()),[],'user agents must be removed after a successful migration')
        migrate()
        self.assertIn('autorestart 0',archive.read_text(encoding='utf-8'),
                      'a re-run must not overwrite the original values with post-change ones')

    def test_revisions_are_pruned_to_the_most_recent_versions(self):
        from app import MAX_REVISIONS
        headers=self.create()
        note='第一版正文'
        self.client.post(f'/api/records/{RID}/edit',headers=headers,
                         json={'title':'Ethics 伦理','note':note,'version':0})
        for version in range(1,MAX_REVISIONS+6):
            response=self.client.post(f'/api/records/{RID}/edit',headers=headers,
                json={'title':'Ethics 伦理','note':f'第 {version} 次修改的正文','version':version})
            self.assertEqual(response.status_code,200,response.text)
        with self.store.db() as db:
            kept=[row[0] for row in db.execute('SELECT version FROM revisions WHERE record=? ORDER BY version',(RID,))]
        self.assertEqual(len(kept),MAX_REVISIONS)
        self.assertEqual(kept[-1],MAX_REVISIONS+5)
        self.assertEqual(self.store.record(RID)['note'],f'第 {MAX_REVISIONS+5} 次修改的正文')

    def test_audio_stream_aborts_instead_of_yielding_a_short_file(self):
        from app import stream_parts
        self.create(b'a'*(PART_SIZE+4))
        with self.store.db() as db:
            db.execute('DELETE FROM parts WHERE record=? AND idx=1',(RID,))
        stream=stream_parts(self.store,RID,2)
        self.assertEqual(len(next(stream)),PART_SIZE)
        with self.assertRaises(RuntimeError):
            next(stream)

    def test_daily_backup_warns_when_the_database_outgrows_the_audio_quota(self):
        import mac_service
        root=Path(self.temp.name)/'Scribelet'
        (root/'data').mkdir(parents=True)
        (root/'backups').mkdir()
        (root/'data'/'scribelet.sqlite3').write_bytes(b'x'*4096)
        errors=io.StringIO()
        with patch.dict(os.environ,{'SCRIBELET_STORAGE_BYTES':'1024'}), patch('sys.stderr',errors):
            mac_service.warn_on_disk_pressure(root)
        self.assertIn('不计入配额',errors.getvalue())
        errors=io.StringIO()
        with patch.dict(os.environ,{'SCRIBELET_STORAGE_BYTES':str(1024**3)}), patch('sys.stderr',errors):
            mac_service.warn_on_disk_pressure(root)
        self.assertNotIn('WARNING 数据库体积',errors.getvalue())

    def test_password_floor_boundaries_and_prompt_agree(self):
        import mac_finish_setup as setup
        floor=setup.MIN_PASSWORD
        self.assertIn(str(floor),setup.PASSWORD_PROMPT,'提示语与实际门槛必须一致')
        short='a'*(floor-1)
        exact='a'*floor
        longest='a'*setup.MAX_PASSWORD
        self.assertFalse(setup.acceptable_password(short,short))
        self.assertTrue(setup.acceptable_password(exact,exact))
        self.assertTrue(setup.acceptable_password(longest,longest))
        self.assertFalse(setup.acceptable_password(longest+'a',longest+'a'))
        self.assertFalse(setup.acceptable_password(exact,exact+'b'),'两次不一致必须拒绝')

    def test_rerun_failure_keeps_the_daemon_plist_when_no_agent_remains(self):
        import pwd
        import subprocess
        import mac_finish_setup
        root,daemons,agents=self.service_fixture()
        # Simulate a machine already migrated: the user agents are gone.
        for plist in list(agents.iterdir()):
            plist.unlink()
        failing=mac_finish_setup.LABELS[0]
        account=pwd.getpwuid(os.getuid())
        fake_run,_,_=self.launchd_model(loaded=['system/'+failing],fail_bootstrap=failing)
        with patch('mac_finish_setup.pwd.getpwnam',return_value=account), \
             patch('mac_finish_setup.subprocess.run',side_effect=fake_run), \
             patch('mac_finish_setup.os.chown'):
            with self.assertRaises(SystemExit):
                mac_finish_setup.install_system(service_root=root,daemon_dir=daemons,agent_dir=agents)
        self.assertTrue((daemons/(failing+'.plist')).exists(),
                        '已迁移的机器上重跑失败时，删掉系统 plist 会让服务彻底消失')

    # ---- 下载完整性、空正文与编辑竞争的回归 ----

    def test_audio_response_declares_content_length(self):
        """没有 Content-Length，中间件会把流中途的异常收成干净的 200+截断。"""
        data=b'b'*(PART_SIZE+7)
        self.create(data)
        response=self.client.get(f'/api/records/{RID}/audio')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.headers.get('content-length'),str(len(data)))
        self.assertEqual(len(response.content),len(data))

    def test_audio_download_rejects_a_part_swapped_after_the_precheck(self):
        from app import stream_parts
        import hashlib
        data=b'c'*(PART_SIZE+7)
        self.create(data)
        expected=[(PART_SIZE,hashlib.sha256(b'c'*PART_SIZE).hexdigest()),
                  (7,hashlib.sha256(b'c'*7).hexdigest())]
        with self.store.db() as db:
            db.execute('UPDATE parts SET data=? WHERE record=? AND idx=1',(b'd'*7,RID))
        stream=stream_parts(self.store,RID,2,expected=expected)
        self.assertEqual(len(next(stream)),PART_SIZE)
        with self.assertRaises(RuntimeError):
            next(stream)

    def test_whitespace_only_note_cannot_clear_without_confirmation(self):
        headers=self.create()
        self.client.post(f'/api/records/{RID}/edit',headers=headers,
                         json={'title':'Ethics 伦理','note':'原始笔记正文','version':0})
        version=1
        for blank in (' ','\t','\n','\u00a0','\ufeff','\u3000'):
            response=self.client.post(f'/api/records/{RID}/edit',headers=headers,
                                      json={'title':'Ethics 伦理','note':blank,'version':version})
            self.assertEqual(response.status_code,400,f'{blank!r} 应被当作空正文: {response.text}')
            self.assertEqual(self.store.record(RID)['note'],'原始笔记正文')
        response=self.client.post(f'/api/records/{RID}/edit',headers=headers,
                                  json={'title':'Ethics 伦理','note':' ','version':version,'allow_empty':True})
        self.assertEqual(response.status_code,200,response.text)

    def test_edit_of_a_record_purged_mid_request_reports_404(self):
        headers=self.create()
        original=self.store.record
        def vanish(rid):
            row=original(rid)
            with self.store.db() as db:
                db.execute('DELETE FROM records WHERE id=?',(rid,))
            self.store.record=original
            return row
        self.store.record=vanish
        response=self.client.post(f'/api/records/{RID}/edit',headers=headers,
                                  json={'title':'T','note':'x','version':0})
        self.assertEqual(response.status_code,404,response.text)

    def test_generation_path_also_prunes_revisions(self):
        """真的走一遍 process_record，而不是在测试里重写那段 SQL。"""
        from app import MAX_REVISIONS
        ffmpeg=test_ffmpeg()
        audio=io.BytesIO()
        with wave.open(audio,'wb') as wav:
            wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(b'\0\0'*32000)
        headers=self.create(audio.getvalue())
        for version in range(MAX_REVISIONS+3):
            response=self.client.post(f'/api/records/{RID}/edit',headers=headers,
                json={'title':'Ethics 伦理','note':f'第 {version} 版','version':version})
            self.assertEqual(response.status_code,200,response.text)
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM revisions WHERE record=?',(RID,)).fetchone()[0],
                             MAX_REVISIONS)
        class Fake:
            def __init__(self,store):pass
            def close(self):pass
            def transcribe(self,path,seconds,language=None):return 'Ethics concerns values.'
            def summarize(self,prompt):return '## Values 价值观\nEthics concerns values. — 伦理涉及价值观。'
        with patch.dict(os.environ,{'SCRIBELET_FFMPEG':ffmpeg}):
            process_record(self.store,RID,provider_factory=Fake)
        self.assertEqual(self.store.record(RID)['state'],'done')
        with self.store.db() as db:
            kept=db.execute('SELECT COUNT(*) FROM revisions WHERE record=?',(RID,)).fetchone()[0]
        self.assertEqual(kept,MAX_REVISIONS,'生成入口也必须裁剪，否则会留到 21 条')

    def test_disk_check_still_runs_when_the_backup_fails(self):
        import mac_service
        calls=[]
        with patch.object(mac_service,'warn_on_disk_pressure',lambda root:calls.append(root)), \
             patch.object(mac_service,'configure',lambda:None), \
             patch('sys.argv',['mac_service.py','backup']), \
             patch.dict('sys.modules',{'backup':type(sys)('backup')}):
            sys.modules['backup'].backup=lambda *a,**k:(_ for _ in ()).throw(OSError('磁盘满'))
            with self.assertRaises(OSError):
                mac_service.main()
        self.assertEqual(len(calls),1,'备份失败时磁盘检查恰好是最该跑的时候')

    # ---- 改密码与服务迁移解耦 ----

    def password_root(self):
        import json as jsonlib
        root=Path(self.temp.name)/'ScribeletRoot'
        (root/'config').mkdir(parents=True)
        (root/'config'/'private.json').write_text(jsonlib.dumps(
            {'SCRIBELET_OWNER':'owner@example.com','SCRIBELET_PASSWORD_HASH':'scrypt$'+'0'*32+'$'+'0'*128}),encoding='utf-8')
        (root/'config'/'initial-password.txt').write_text('placeholder',encoding='utf-8')
        return root

    def test_password_change_on_a_migrated_machine_never_touches_launchd(self):
        import json as jsonlib
        import mac_finish_setup
        root=self.password_root()
        before=(root/'config'/'private.json').read_bytes()
        commands=[]
        with patch('mac_finish_setup.migrated',return_value=True), \
             patch('mac_finish_setup.launch_root_installer') as installer, \
             patch('mac_finish_setup.install_system') as install, \
             patch('mac_finish_setup.getpass.getpass',return_value='a-fresh-password'), \
             patch('mac_finish_setup.subprocess.run',
                   side_effect=lambda command,**kw:(commands.append(command),
                       __import__('subprocess').CompletedProcess(command,0,b'',b''))[1]), \
             patch('sys.argv',['mac_finish_setup.py']):
            mac_finish_setup.main(root=root)
        installer.assert_not_called()
        install.assert_not_called()
        self.assertNotEqual((root/'config'/'private.json').read_bytes(),before,'密码应当已更新')
        self.assertFalse((root/'config'/'initial-password.txt').exists())
        self.assertEqual(commands,[['sudo','launchctl','kickstart','-k','system/com.scribelet.app']],
                         '只应重启系统域的 app，不得有 bootout/bootstrap/pmset')
        values=jsonlib.loads((root/'config'/'private.json').read_text(encoding='utf-8'))
        self.assertEqual(values['SCRIBELET_OWNER'],'owner@example.com','其余配置必须原样保留')

    def test_failed_migration_leaves_the_password_unchanged(self):
        import subprocess
        import mac_finish_setup
        root=self.password_root()
        before=(root/'config'/'private.json').read_bytes()
        def boom():
            raise subprocess.CalledProcessError(1,['sudo','installer'])
        with patch('mac_finish_setup.migrated',return_value=False), \
             patch('mac_finish_setup.launch_root_installer',side_effect=boom), \
             patch('mac_finish_setup.getpass.getpass',return_value='a-fresh-password'), \
             patch('sys.argv',['mac_finish_setup.py']):
            with self.assertRaises(subprocess.CalledProcessError):
                mac_finish_setup.main(root=root)
        self.assertEqual((root/'config'/'private.json').read_bytes(),before,
                         '迁移失败时密码必须一个字节都没动')
        self.assertTrue((root/'config'/'initial-password.txt').exists())

    def test_reload_targets_the_domain_that_owns_the_app(self):
        import subprocess
        import mac_finish_setup
        seen=[]
        ok=lambda command,**kw:(seen.append(command),subprocess.CompletedProcess(command,0,b'',b''))[1]
        with patch('mac_finish_setup.subprocess.run',side_effect=ok):
            self.assertTrue(mac_finish_setup.reload_app(system=True))
            self.assertTrue(mac_finish_setup.reload_app(system=False))
        self.assertEqual(seen[0],['sudo','launchctl','kickstart','-k','system/com.scribelet.app'])
        self.assertEqual(seen[1],['launchctl','kickstart','-k','gui/%d/com.scribelet.app'%os.getuid()])
        fail=lambda command,**kw:subprocess.CompletedProcess(command,1,b'',b'')
        errors=io.StringIO()
        with patch('mac_finish_setup.subprocess.run',side_effect=fail), patch('sys.stderr',errors):
            self.assertFalse(mac_finish_setup.reload_app(system=True))
        self.assertIn('仍在用旧密码',errors.getvalue(),'重启失败必须明说新密码尚未生效')

    def test_migrated_detects_the_system_domain(self):
        import mac_finish_setup
        root,daemons,agents=self.service_fixture()
        with patch('mac_finish_setup.loaded', return_value=True):
            self.assertFalse(mac_finish_setup.migrated(daemon_dir=daemons))
            for label in mac_finish_setup.LABELS:
                (daemons/(label+'.plist')).write_bytes(b'x')
            self.assertTrue(mac_finish_setup.migrated(daemon_dir=daemons))
            (daemons/(mac_finish_setup.LABELS[0]+'.plist')).unlink()
            self.assertFalse(mac_finish_setup.migrated(daemon_dir=daemons),'缺一个就不算已迁移')

    # ---- 迁移中的三处静默失败 ----

    def test_migration_aborts_when_the_old_job_will_not_stop(self):
        """停不掉旧任务却继续装新的 = 两个 Scribelet 同时抢同一个数据库。"""
        import pwd
        import mac_finish_setup
        root,daemons,agents=self.service_fixture()
        account=pwd.getpwuid(os.getuid())
        stubborn=mac_finish_setup.LABELS[0]
        fake_run,state,calls=self.launchd_model(
            loaded=['gui/%d/%s'%(account.pw_uid,label) for label in mac_finish_setup.LABELS],
            refuses_to_stop=['gui/%d/%s'%(account.pw_uid,stubborn)])
        with patch('mac_finish_setup.pwd.getpwnam',return_value=account), \
             patch('mac_finish_setup.subprocess.run',side_effect=fake_run), \
             patch('mac_finish_setup.os.chown'):
            with self.assertRaises(SystemExit) as stopped:
                mac_finish_setup.install_system(service_root=root,daemon_dir=daemons,agent_dir=agents)
        self.assertIn('没能停下',str(stopped.exception))
        self.assertNotIn('未改动任何服务',str(stopped.exception),'前面若已迁移过别的服务，这句话就是假的')
        self.assertIn('gui/%d/%s'%(account.pw_uid,stubborn),state,'旧任务仍在跑')
        self.assertNotIn('system/'+stubborn,state,'绝不能在旧任务还活着时启动系统级任务')
        self.assertEqual(list(daemons.glob('*.plist')),[],'中止时不得留下系统 plist')
        self.assertEqual(list(daemons.glob('*.new')),[],'暂存文件必须清掉')
        self.assertTrue((agents/(stubborn+'.plist')).exists(),'用户启动项不得被删')

    def test_a_failed_rollback_is_reported_as_failed(self):
        """回滚也失败时必须说实话，不能照样打印「已回滚」。"""
        import pwd
        import subprocess
        import mac_finish_setup
        root,daemons,agents=self.service_fixture()
        account=pwd.getpwuid(os.getuid())
        failing=mac_finish_setup.LABELS[0]
        domain='gui/%d'%account.pw_uid
        def fake_run(command,**kwargs):
            if command[:2]==['launchctl','print']:
                return subprocess.CompletedProcess(command,1,'','')
            if command[:3]==['launchctl','bootstrap','system']:
                raise subprocess.CalledProcessError(1,command)
            if command[:2]==['launchctl','bootstrap'] and command[2]==domain:
                return subprocess.CompletedProcess(command,1,'','')   # 回滚也失败
            if command[:2]==['pmset','-g']:
                return subprocess.CompletedProcess(command,0,'AC Power:\n autorestart 0\n','')
            return subprocess.CompletedProcess(command,0,'','')
        with patch('mac_finish_setup.pwd.getpwnam',return_value=account), \
             patch('mac_finish_setup.subprocess.run',side_effect=fake_run), \
             patch('mac_finish_setup.os.chown'):
            with self.assertRaises(SystemExit) as stopped:
                mac_finish_setup.install_system(service_root=root,daemon_dir=daemons,agent_dir=agents)
        message=str(stopped.exception)
        self.assertNotIn('已回滚',message,'回滚失败却说已回滚就是在骗人')
        self.assertIn('回滚也失败',message)
        self.assertIn('launchctl bootstrap '+domain,message,'必须给出可照抄的恢复命令')

    def test_a_bad_plist_write_never_stops_a_working_service(self):
        """先暂存校验再停服务：写坏了也不会留下「旧的停了、新的是残文件」。"""
        import pwd
        import mac_finish_setup
        root,daemons,agents=self.service_fixture()
        account=pwd.getpwuid(os.getuid())
        fake_run,state,calls=self.launchd_model(
            loaded=['gui/%d/%s'%(account.pw_uid,label) for label in mac_finish_setup.LABELS])
        with patch('mac_finish_setup.pwd.getpwnam',return_value=account), \
             patch('mac_finish_setup.subprocess.run',side_effect=fake_run), \
             patch('mac_finish_setup.os.chown'), \
             patch('mac_finish_setup.plistlib.dumps',return_value=b'truncated-not-a-plist'):
            with self.assertRaises(SystemExit) as stopped:
                mac_finish_setup.install_system(service_root=root,daemon_dir=daemons,agent_dir=agents)
        self.assertIn('校验失败',str(stopped.exception))
        self.assertFalse([c for c in calls if c[:2]==['launchctl','bootout']],
                         '校验没过就不该停任何服务')
        self.assertEqual(len(state),len(mac_finish_setup.LABELS),'原有任务必须全部还活着')
        self.assertEqual(list(daemons.glob('*')),[],'不得留下任何残文件')

    def test_abort_message_names_services_already_migrated(self):
        """第二个服务中止时，第一个已经迁移完了——信息里必须如实说。"""
        import pwd
        import mac_finish_setup
        root,daemons,agents=self.service_fixture()
        account=pwd.getpwuid(os.getuid())
        first,second=mac_finish_setup.LABELS[0],mac_finish_setup.LABELS[1]
        fake_run,state,_=self.launchd_model(
            loaded=['gui/%d/%s'%(account.pw_uid,label) for label in mac_finish_setup.LABELS],
            refuses_to_stop=['gui/%d/%s'%(account.pw_uid,second)])
        with patch('mac_finish_setup.pwd.getpwnam',return_value=account), \
             patch('mac_finish_setup.subprocess.run',side_effect=fake_run), \
             patch('mac_finish_setup.os.chown'):
            with self.assertRaises(SystemExit) as stopped:
                mac_finish_setup.install_system(service_root=root,daemon_dir=daemons,agent_dir=agents)
        self.assertIn(first,str(stopped.exception),'已迁移的服务必须点名')
        self.assertIn('system/'+first,state)

    # ---- 处理状态不冒充课程内容 ----

    def test_no_speech_marker_is_archived_but_never_fed_to_the_model(self):
        from processing import NO_SPEECH
        ffmpeg=test_ffmpeg()
        audio=io.BytesIO()
        with wave.open(audio,'wb') as wav:
            wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(b'\0\0'*16000*120)
        self.create(audio.getvalue())          # 120 秒 → 3 段（45/45/30）
        prompts=[]
        class Fake:
            def __init__(self,store):pass
            def close(self):pass
            def transcribe(self,path,seconds,language=None):
                Fake.n=getattr(Fake,'n',0)+1
                return 'Only the second chunk has words.' if Fake.n==2 else ''
            def summarize(self,prompt):prompts.append(prompt);return '## 摘要'
        with patch.dict(os.environ,{'SCRIBELET_FFMPEG':ffmpeg}):
            process_record(self.store,RID,provider_factory=Fake)
        note=self.store.record(RID)['note']
        summaries=[p for p in prompts if '<course_material>' in p]
        self.assertEqual(len(summaries),1)
        for prompt in prompts:
            self.assertNotIn(NO_SPEECH,prompt,'标记是处理状态，不能当作老师说的话喂给模型')
        self.assertIn('Only the second chunk has words.',summaries[0])
        self.assertIn(NO_SPEECH,note,'归档转写里仍要保留带时间戳的标记')
        self.assertIn('录音质量可能有问题',note,'2/3 段为空必须在笔记顶部提醒')
        self.assertIn('67%',note)

    def test_clean_recording_gets_no_quality_caution(self):
        from processing import speech_coverage, NO_SPEECH
        self.assertEqual(speech_coverage({0:'[00:00] a',1:'[00:45] b'}),{'empty_ratio':0.0,'longest_gap':0})
        self.assertEqual(speech_coverage({0:'[00:00] '+NO_SPEECH,1:'[00:45] '+NO_SPEECH,2:'[01:30] x',3:'[02:15] '+NO_SPEECH}),
                         {'empty_ratio':0.75,'longest_gap':2})
        self.assertEqual(speech_coverage({}),{'empty_ratio':0.0,'longest_gap':0})

    # ---- 转写质量：表情标记、停顿处切分、中英对照 ----

    def test_sensevoice_marks_are_stripped_from_transcripts(self):
        from processing import clean_transcript
        self.assertEqual(clean_transcript('we get.😊 <|HAPPY|> To angry 👏'),'we get. To angry')
        self.assertEqual(clean_transcript('  spaced   out  '),'spaced out')
        self.assertEqual(clean_transcript('中文 也 保留 ✓'),'中文 也 保留')

    def test_segments_are_cut_inside_pauses_when_one_is_near(self):
        from processing import plan_segments
        # 停顿在 47–48 秒：应当在停顿里切，而不是硬切 45
        plan=plan_segments(100.0,[(47.0,48.0)])
        self.assertEqual(plan[0][0],0.0)
        self.assertTrue(47.15<=plan[0][1]<=47.85,plan)
        self.assertEqual(plan[-1][1],100.0)
        # 窗口内没有停顿：退回目标点
        self.assertEqual(plan_segments(100.0,[(70.0,71.0)])[0][1],45.0)
        # 太短或超出窗口的停顿不采用；段长绝不超过 maximum
        plan=plan_segments(400.0,[(10.0,10.2),(52.5,53.5)])
        self.assertTrue(all(e-s<=55.0 for s,e in plan),plan)
        self.assertTrue(52.6<=plan[0][1]<=53.4,plan)
        # 没有停顿信息：固定 45 秒，且对同一输入结果确定
        self.assertEqual(plan_segments(100.0,[]),plan_segments(100.0,[]))
        self.assertEqual(plan_segments(100.0,[])[0],(0.0,45.0))
        # 短音频只有一段
        self.assertEqual(plan_segments(30.0,[(12.0,13.0)]),[(0.0,30.0)])

    def test_translation_failures_keep_the_english_transcript(self):
        from processing import translate_segments
        class Broken:
            def summarize(self,prompt):raise ValueError('限流')
        self.assertEqual(translate_segments(Broken(),[(0,'[00:00] hello'),(1,'[00:45] world')]),({},'限流'))
        class Partial:
            def __init__(self):self.prompts=[]
            def summarize(self,prompt):self.prompts.append(prompt);return '[0] 你好\n乱七八糟的一行\n[7] 不存在的编号'
        p=Partial()
        got,failure=translate_segments(p,[(0,'[00:00] hello'),(1,'[00:45] world')])
        self.assertEqual(got,{0:'你好\n乱七八糟的一行'},'续行保留；无法在语法层判断译文语义');self.assertIn('格式异常',failure)
        self.assertNotIn('[00:00]',p.prompts[0],'送翻译的段落不带时间戳')
        self.assertIn('[1] world',p.prompts[0])

if __name__=='__main__':unittest.main()
