"""Single-owner Scribelet service. Run one Uvicorn process behind the bundled TLS proxy."""
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

HERE = Path(__file__).resolve().parent
PART_SIZE = 2 * 1024 * 1024
MAX_FILE = 256 * 1024 * 1024
MAX_NOTE = 400_000
MAX_NOTE_REQUEST = 2_000_000
MAX_REVISIONS = 20
# Must cover the worst-case reservation of one 4-hour recording (~1.1M at fast speech when the provider reports no usage); see tests/test_token_budget.py.
DAILY_TOKENS = '1200000'
COOKIE = '__Host-scribelet'


def stream_parts(store, rid, count, expected=None):
    """Yield a record's audio parts, refusing to finish a 200 with a short file."""
    for idx in range(count):
        with store.db() as db:
            part=db.execute('SELECT data FROM parts WHERE record=? AND idx=?',(rid,idx)).fetchone()
        if not part:
            raise RuntimeError('录音分片在下载过程中消失，已中止传输')
        if expected is not None and (len(part[0]), hashlib.sha256(part[0]).hexdigest()) != expected[idx]:
            raise RuntimeError('录音分片在下载过程中改变，已中止传输')
        yield part[0]


def valid_title(value):
    return isinstance(value, str) and 1 <= len(value.strip()) <= 200 and '\n' not in value and '\r' not in value


def blank_note(value):
    # Match ECMAScript String.trim(), including BOM and excluding U+0085.
    return not value.strip('\u0009\u000a\u000b\u000c\u000d\u0020\u00a0\u1680'
                           '\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007'
                           '\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff')


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return f'scrypt${salt}${digest}'


def backup_warning(store):
    """Surface offsite freshness in the app even when macOS notifications are muted."""
    service_root = store.root.parent
    if not (service_root / 'config/offsite.json').exists():
        return ''
    record = service_root / 'logs/offsite-last-success.json'
    if not record.exists():
        return '异地备份尚未成功；请在 Mac 上检查备份任务。'
    try:
        value = json.loads(record.read_text(encoding='utf-8'))
        finished = datetime.fromisoformat(value['finished_utc'])
        if finished.tzinfo is None:
            raise ValueError()
        age = (datetime.now(timezone.utc) - finished).total_seconds()
        if age < -300:
            return '异地备份时间在未来；请检查 Mac 时钟。'
        if age > 26 * 3600:
            return '异地备份已超过 26 小时未成功；请检查 Mac。'
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return '异地备份状态文件损坏；请在 Mac 上检查备份任务。'
    return ''


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'scribelet.sqlite3'
        with self.db() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, csrf TEXT, expires REAL, created REAL);
            CREATE TABLE IF NOT EXISTS attempts (bucket INTEGER PRIMARY KEY, count INTEGER);
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS usage (day TEXT PRIMARY KEY, jobs INTEGER DEFAULT 0, audio REAL DEFAULT 0, tokens INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS records (
                id TEXT PRIMARY KEY, title TEXT, size INTEGER, mime TEXT, state TEXT,
                created REAL, updated REAL, error TEXT DEFAULT '', note TEXT DEFAULT '',
                version INTEGER DEFAULT 0, duration REAL DEFAULT 0, deleted INTEGER DEFAULT 0,
                progress INTEGER DEFAULT 0, manual TEXT DEFAULT '');
            CREATE TABLE IF NOT EXISTS parts (record TEXT, idx INTEGER, digest TEXT, data BLOB,
                PRIMARY KEY(record,idx));
            CREATE TABLE IF NOT EXISTS segments (record TEXT, idx INTEGER, text TEXT, PRIMARY KEY(record,idx));
            CREATE TABLE IF NOT EXISTS submission_receipts (record TEXT, key TEXT, PRIMARY KEY(record,key));
            CREATE TABLE IF NOT EXISTS segment_plans (record TEXT PRIMARY KEY, plan TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS model_results (record TEXT, key TEXT, result TEXT NOT NULL, PRIMARY KEY(record,key));
            CREATE TABLE IF NOT EXISTS revisions (record TEXT, version INTEGER, title TEXT, note TEXT, created REAL,
                PRIMARY KEY(record,version));
            ''')
            if 'rebuild_note' not in {r[1] for r in db.execute('PRAGMA table_info(records)')}:
                db.execute('ALTER TABLE records ADD COLUMN rebuild_note INTEGER DEFAULT 0')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=20)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def record(self, rid):
        if not re.fullmatch(r'[a-f0-9]{32}', rid):
            raise HTTPException(404, '记录不存在')
        with self.db() as db:
            row = db.execute('SELECT * FROM records WHERE id=?', (rid,)).fetchone()
        if not row:
            raise HTTPException(404, '记录不存在')
        return dict(row)

    def reserve(self, jobs=0, audio=0, tokens=0):
        values = (jobs, audio, tokens)
        if any(type(value) not in (int, float) or not math.isfinite(value) or value < 0 for value in values):
            raise ValueError('用量预留值无效')
        day = datetime.now(timezone.utc).date().isoformat()
        limits = {'jobs': int(os.getenv('SCRIBELET_DAILY_JOBS', '12')),
                  'audio': int(os.getenv('SCRIBELET_DAILY_AUDIO_SECONDS', '14400')),
                  'tokens': int(os.getenv('SCRIBELET_DAILY_TOKENS', DAILY_TOKENS))}
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO usage(day) VALUES(?)', (day,))
            row = db.execute('SELECT * FROM usage WHERE day=?', (day,)).fetchone()
            if any(row[k] + v > limits[k] for k, v in {'jobs': jobs, 'audio': audio, 'tokens': tokens}.items()):
                raise ValueError('今日用量已达上限，录音已保留，请次日重试。')
            db.execute('UPDATE usage SET jobs=jobs+?,audio=audio+?,tokens=tokens+? WHERE day=?', (jobs,audio,tokens,day))
        return day

    def release_tokens(self, day, tokens):
        """Return an over-reservation once the provider reports what a call really used."""
        if type(tokens) is not int or tokens <= 0:
            return
        with self.db() as db:
            db.execute('UPDATE usage SET tokens=MAX(tokens-?,0) WHERE day=?', (tokens,day))


async def body_bytes(request, limit):
    body = bytearray()
    async for block in request.stream():
        body.extend(block)
        if len(body) > limit:
            raise HTTPException(413, '内容超过大小限制')
    return bytes(body)


async def body_json(request, limit=65536):
    try:
        value = json.loads(await body_bytes(request, limit))
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, '请求格式错误')


def create_app(root=None, origin=None, owner=None, password=None, start_worker=True, preview=False):
    origin = (origin or os.environ.get('SCRIBELET_ORIGIN', '')).rstrip('/')
    parsed = urlsplit(origin)
    if parsed.path or parsed.query or parsed.fragment or parsed.username or not parsed.hostname:
        raise ValueError('SCRIBELET_ORIGIN 必须是完整站点源地址')
    if preview and parsed.hostname not in ('127.0.0.1','localhost','testserver'):
        raise ValueError('预览模式只能用于本机测试，不能发布到公网')
    if parsed.scheme != 'https' and not (preview and parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost', 'testserver')):
        raise ValueError('公网服务必须使用 HTTPS')
    owner = owner or os.environ.get('SCRIBELET_OWNER', '')
    password = password or os.environ.get('SCRIBELET_PASSWORD_HASH', '')
    if not owner or not re.fullmatch(r'scrypt\$[a-f0-9]{32}\$[a-f0-9]{128}', password):
        raise ValueError('必须先配置唯一账号和密码哈希；没有默认账号或开放注册')
    store = Store(root or os.environ.get('SCRIBELET_DATA', str(HERE / 'data')))
    with store.db() as db:
        fingerprint=hashlib.sha256((owner+'\0'+password).encode()).hexdigest()
        old=db.execute("SELECT value FROM metadata WHERE key='credential_version'").fetchone()
        if not old or old[0]!=fingerprint:
            db.execute('DELETE FROM sessions')
            db.execute("INSERT OR REPLACE INTO metadata VALUES('credential_version',?)",(fingerprint,))
    cookie = 'scribelet_preview' if preview else COOKIE
    stopped = threading.Event()

    @asynccontextmanager
    async def lifespan(app):
        thread = None
        if start_worker:
            from processing import run_worker
            thread = threading.Thread(target=run_worker, args=(store, stopped), daemon=True)
            thread.start()
        yield
        stopped.set()
        if thread:
            thread.join(timeout=3)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    assets = {'/': ('index.html','text/html'), '/app.js': ('app.js','text/javascript'),
              '/style.css': ('style.css','text/css'), '/sw.js': ('sw.js','text/javascript'),
              '/manifest.webmanifest': ('manifest.webmanifest','application/manifest+json')}
    public = set(assets) | {'/api/login','/api/health'}

    @app.middleware('http')
    async def guard(request, call_next):
        if request.headers.get('host') != parsed.netloc:
            return JSONResponse({'detail': '站点地址不匹配'}, 400)
        path = request.url.path
        if path not in public:
            raw = request.cookies.get(cookie, '')
            sid = hashlib.sha256(raw.encode()).hexdigest()
            with store.db() as db:
                session = db.execute('SELECT * FROM sessions WHERE id=? AND expires>?', (sid,time.time())).fetchone()
            if not session:
                return JSONResponse({'detail':'请登录你的私人账号'}, 401)
            request.state.session = dict(session)
        if request.method not in ('GET','HEAD','OPTIONS'):
            if request.headers.get('origin') != origin:
                return JSONResponse({'detail':'请求来源不匹配'}, 403)
            if path != '/api/login' and (path in public or not hmac.compare_digest(request.headers.get('x-csrf-token',''), request.state.session['csrf'])):
                return JSONResponse({'detail':'登录状态校验失败，请刷新后重试'}, 403)
        response = await call_next(request)
        response.headers.update({'Cache-Control':'no-store', 'X-Content-Type-Options':'nosniff',
            'Referrer-Policy':'no-referrer', 'X-Frame-Options':'DENY',
            'Permissions-Policy':'microphone=(self), camera=(), geolocation=()',
            'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; media-src 'self' blob:; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"})
        if not preview:
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        return response

    @app.get('/api/health')
    def health():
        return {'ok': True, 'app':'ScribeletPrivate'}

    @app.post('/api/login')
    async def login(request: Request):
        body = await body_json(request, 4096)
        # Global, persistent rate limit cannot be bypassed by spoofing proxy headers.
        bucket = int(time.time() // 900)
        with store.db() as db:
            db.execute('DELETE FROM attempts WHERE bucket<?', (bucket-1,))
            db.execute('INSERT OR IGNORE INTO attempts VALUES(?,0)', (bucket,))
            attempts = db.execute('SELECT count FROM attempts WHERE bucket=?', (bucket,)).fetchone()[0]
            if attempts >= 30:
                raise HTTPException(429, '尝试次数过多，请稍后再试')
            db.execute('UPDATE attempts SET count=count+1 WHERE bucket=?', (bucket,))
        supplied = body.get('password', '')
        name = body.get('username', '')
        if not isinstance(supplied,str) or not isinstance(name,str) or len(supplied)>256:
            raise HTTPException(401,'账号或密码不正确')
        valid = hmac.compare_digest(password_hash(supplied, password.split('$')[1]), password)
        if not (valid and hmac.compare_digest(name.encode(),owner.encode())):
            raise HTTPException(401,'账号或密码不正确')
        raw, csrf, now = secrets.token_urlsafe(32), secrets.token_urlsafe(32), time.time()
        with store.db() as db:
            db.execute('DELETE FROM sessions WHERE expires<?', (now,))
            db.execute('INSERT INTO sessions VALUES(?,?,?,?)', (hashlib.sha256(raw.encode()).hexdigest(),csrf,now+7*86400,now))
            db.execute('DELETE FROM sessions WHERE id NOT IN (SELECT id FROM sessions ORDER BY created DESC LIMIT 8)')
        response = JSONResponse({'ok':True})
        response.set_cookie(cookie,raw,max_age=7*86400,secure=not preview,httponly=True,samesite='strict',path='/')
        return response

    @app.get('/api/me')
    def me(request: Request):
        from processing import api_key, note_model
        with store.db() as db:
            usage = [dict(r) for r in db.execute('SELECT * FROM usage ORDER BY day DESC LIMIT 1')]
        return {'owner':owner,'csrf':request.state.session['csrf'],'usage':usage,
                'backup_warning':backup_warning(store),
                'key_ready':bool(api_key()),
                'limits':{'jobs':int(os.getenv('SCRIBELET_DAILY_JOBS','12')),'audio_seconds':int(os.getenv('SCRIBELET_DAILY_AUDIO_SECONDS','14400')),'tokens':int(os.getenv('SCRIBELET_DAILY_TOKENS',DAILY_TOKENS))},
                'model':note_model()}

    @app.post('/api/logout')
    def logout(request: Request):
        with store.db() as db:
            db.execute('DELETE FROM sessions WHERE id=?',(request.state.session['id'],))
        response = JSONResponse({'ok':True})
        response.delete_cookie(cookie,path='/',secure=not preview,httponly=True,samesite='strict')
        return response

    @app.post('/api/revoke-others')
    def revoke(request: Request):
        with store.db() as db:
            db.execute('DELETE FROM sessions WHERE id<>?',(request.state.session['id'],))
        return {'ok':True}

    @app.get('/api/records')
    def records():
        with store.db() as db:
            return [dict(r) for r in db.execute('SELECT id,title,size,state,created,updated,error,version,deleted,progress FROM records ORDER BY created DESC LIMIT 500')]

    @app.post('/api/import-note')
    async def import_note(request: Request):
        body=await body_json(request,MAX_NOTE_REQUEST)
        rid,title,note=body.get('id'),body.get('title'),body.get('note')
        if not isinstance(rid,str) or not re.fullmatch(r'[a-f0-9]{32}',rid) or not valid_title(title) or not isinstance(note,str) or not 0<len(note)<=MAX_NOTE:
            raise HTTPException(400,'导入笔记格式或长度无效')
        with store.db() as db:
            old=db.execute('SELECT note FROM records WHERE id=?',(rid,)).fetchone()
            if old:
                if old[0]!=note:raise HTTPException(409,'导入标识冲突')
                return {'id':rid}
            if db.execute('SELECT COUNT(*) FROM records').fetchone()[0]>=500:
                raise HTTPException(413,'笔记数量已达上限')
            now=time.time()
            db.execute("INSERT INTO records(id,title,size,mime,state,created,updated,note,progress) VALUES(?,?,0,'text/markdown','done',?,?,?,100)",(rid,title.strip(),now,now,note))
        return {'id':rid}

    @app.post('/api/records')
    async def create_record(request: Request):
        body = await body_json(request)
        rid, title, size, mime = body.get('id'),body.get('title'),body.get('size'),body.get('mime','')
        if not isinstance(rid,str) or not re.fullmatch(r'[a-f0-9]{32}',rid) or not valid_title(title) or type(size) is not int or not 0<size<=MAX_FILE or not isinstance(mime,str) or len(mime)>120:
            raise HTTPException(400,'文件信息无效；单份最大 256 MB')
        with store.db() as db:
            existing = db.execute('SELECT * FROM records WHERE id=?',(rid,)).fetchone()
            if existing:
                if existing['size'] != size or existing['mime'] != mime:
                    raise HTTPException(409,'上传标识已被其他文件占用')
                return {'id':rid}
            size_sum = db.execute('SELECT COALESCE(SUM(size),0) FROM records').fetchone()[0]
            count = db.execute('SELECT COUNT(*) FROM records').fetchone()[0]
            if size_sum+size>int(os.getenv('SCRIBELET_STORAGE_BYTES',str(2*1024**3))) or count>=500:
                raise HTTPException(413,'存储空间已达上限，请备份后联系管理员清理')
            now = time.time()
            db.execute('INSERT INTO records(id,title,size,mime,state,created,updated) VALUES(?,?,?,?,?,?,?)',(rid,title.strip(),size,mime,'uploading',now,now))
        return {'id':rid}

    @app.get('/api/records/{rid}')
    def read_record(rid: str):
        record = store.record(rid)
        with store.db() as db:
            record['parts'] = [r[0] for r in db.execute('SELECT idx FROM parts WHERE record=? ORDER BY idx',(rid,))]
            record['transcript'] = '\n\n'.join(r[0] for r in db.execute('SELECT text FROM segments WHERE record=? ORDER BY idx',(rid,)))
        return record

    @app.put('/api/records/{rid}/parts/{idx}')
    async def upload_part(rid: str, idx: int, request: Request):
        record = store.record(rid)
        if idx<0 or idx>=math.ceil(record['size']/PART_SIZE):
            raise HTTPException(400,'分片编号无效')
        data = await body_bytes(request, PART_SIZE)
        expected = min(PART_SIZE, record['size']-idx*PART_SIZE)
        if len(data)!=expected:
            raise HTTPException(400,'分片长度不匹配')
        digest = hashlib.sha256(data).hexdigest()
        with store.db() as db:
            state = db.execute('SELECT state,deleted FROM records WHERE id=?',(rid,)).fetchone()
            if state['state']!='uploading' or state['deleted']:
                raise HTTPException(409,'记录已提交或已删除')
            old = db.execute('SELECT digest FROM parts WHERE record=? AND idx=?',(rid,idx)).fetchone()
            if old and old[0]!=digest:
                raise HTTPException(409,'分片内容冲突，原数据未覆盖')
            db.execute('INSERT OR IGNORE INTO parts VALUES(?,?,?,?)',(rid,idx,digest,data))
        return {'ok':True,'sha256':digest}

    @app.post('/api/records/{rid}/submit')
    async def submit(rid: str, request: Request):
        body=await body_json(request)
        submission_id=body.get('submission_id')
        if submission_id is not None and (not isinstance(submission_id,str) or not re.fullmatch(r'[a-f0-9]{32}',submission_id)):
            raise HTTPException(400,'提交标识无效')
        record = store.record(rid)
        with store.db() as db:
            row = db.execute('SELECT * FROM records WHERE id=?',(rid,)).fetchone()
            if row['deleted']:
                raise HTTPException(409,'请先恢复记录')
            if submission_id is not None:
                if db.execute('SELECT 1 FROM submission_receipts WHERE record=? AND key=?',(rid,submission_id)).fetchone():
                    return {'ok':True,'state':row['state']}
                db.execute('INSERT INTO submission_receipts VALUES(?,?)',(rid,submission_id))
            if row['state'] in ('queued','processing','done'):
                return {'ok':True,'state':row['state']}
            count,total = db.execute('SELECT COUNT(*),COALESCE(SUM(LENGTH(data)),0) FROM parts WHERE record=?',(rid,)).fetchone()
            if total!=record['size'] or count!=math.ceil(record['size']/PART_SIZE):
                raise HTTPException(409,'录音尚未上传完整')
            db.execute("UPDATE records SET state='queued',error='',updated=? WHERE id=?",(time.time(),rid))
        return {'ok':True,'state':'queued'}

    @app.post('/api/records/{rid}/regenerate')
    def regenerate(rid: str):
        store.record(rid)
        with store.db() as db:
            row=db.execute('SELECT * FROM records WHERE id=?',(rid,)).fetchone()
            if row['deleted']:
                raise HTTPException(409,'请先恢复记录')
            if row['state'] in ('queued','processing'):
                return {'ok':True,'state':row['state']}
            if (row['state']!='done' and not (row['state']=='error' and row['rebuild_note'])) or not row['size'] or not db.execute('SELECT 1 FROM segments WHERE record=? LIMIT 1',(rid,)).fetchone():
                raise HTTPException(409,'需要已有音频转写才能重新生成笔记')
            db.execute("UPDATE records SET state='queued',rebuild_note=1,error='',updated=? WHERE id=?",(time.time(),rid))
        return {'ok':True,'state':'queued'}

    @app.post('/api/records/{rid}/edit')
    async def edit(rid: str, request: Request):
        store.record(rid)
        body = await body_json(request, MAX_NOTE_REQUEST)
        title,note,version = body.get('title'),body.get('note'),body.get('version')
        if not valid_title(title) or not isinstance(note,str) or len(note)>MAX_NOTE:
            raise HTTPException(400,'标题或正文长度无效')
        with store.db() as db:
            row = db.execute('SELECT * FROM records WHERE id=?',(rid,)).fetchone()
            if row is None:
                raise HTTPException(404,'记录不存在')
            if row['version']!=version or row['state'] in ('queued','processing'):
                raise HTTPException(409,'内容已更新或正在生成，请重新打开；你的编辑仍在输入框内')
            # An empty body only ever makes sense as a deliberate clear: never let
            # it silently overwrite existing text, which has no recovery UI.
            if blank_note(note) and row['note'] and body.get('allow_empty') is not True:
                raise HTTPException(400,'正文为空会清空整篇笔记。请在页面上确认后重试，或把整条记录移入回收站。')
            db.execute('INSERT INTO revisions VALUES(?,?,?,?,?)',(rid,row['version'],row['title'],row['note'],time.time()))
            # Revision bodies are not counted by the storage quota, so bound them here.
            db.execute('DELETE FROM revisions WHERE record=? AND version NOT IN '
                       '(SELECT version FROM revisions WHERE record=? ORDER BY version DESC LIMIT ?)',
                       (rid,rid,MAX_REVISIONS))
            db.execute('UPDATE records SET title=?,note=?,version=version+1,updated=? WHERE id=?',(title.strip(),note,time.time(),rid))
        return {'ok':True}

    @app.post('/api/records/{rid}/trash')
    async def trash(rid: str, request: Request):
        store.record(rid)
        body = await body_json(request)
        if type(body.get('deleted')) is not bool:
            raise HTTPException(400,'删除状态无效')
        with store.db() as db:
            row = db.execute('SELECT state FROM records WHERE id=?',(rid,)).fetchone()
            if row[0] in ('processing','queued'):
                raise HTTPException(409,'请等待生成结束')
            db.execute('UPDATE records SET deleted=?,updated=? WHERE id=?',(int(body['deleted']),time.time(),rid))
        return {'ok':True}

    @app.post('/api/records/{rid}/purge')
    def purge(rid: str):
        store.record(rid)
        with store.db() as db:
            row = db.execute('SELECT deleted,state FROM records WHERE id=?',(rid,)).fetchone()
            if not row['deleted']:
                raise HTTPException(409,'请先把记录移入回收站')
            if row['state'] in ('processing','queued'):
                raise HTTPException(409,'请等待生成结束')
            for table in ('parts','segments','revisions','segment_plans','model_results','submission_receipts'):
                db.execute(f'DELETE FROM {table} WHERE record=?',(rid,))
            db.execute('DELETE FROM records WHERE id=?',(rid,))
        return {'ok':True}

    @app.get('/api/records/{rid}/audio')
    def audio(rid: str):
        from starlette.responses import StreamingResponse
        record=store.record(rid)
        if record['size'] == 0:
            raise HTTPException(409,'这是一条纯文本笔记，没有原始音频。')
        expected=[]
        if record['size']:
            with store.db() as db:
                current=db.execute('SELECT created FROM records WHERE id=?',(rid,)).fetchone()
                if current is None or current['created']!=record['created']:
                    raise HTTPException(409,'录音已改变，请重新打开后下载。')
                parts=db.execute('SELECT idx,LENGTH(data),digest FROM parts WHERE record=? ORDER BY idx',(rid,)).fetchall()
            expected=[(part[1],part[2]) for part in parts]
            if (len(parts)!=math.ceil(record['size']/PART_SIZE) or
                sum(size for size,_ in expected)!=record['size'] or
                any(part[0]!=idx for idx,part in enumerate(parts))):
                raise HTTPException(409,'录音尚未上传完整，请先续传完成再下载原音频。')
        chunks=stream_parts(store,rid,math.ceil(record['size']/PART_SIZE),expected=expected)
        mime=record['mime'].split(';')[0]
        extension={'audio/webm':'webm','video/webm':'webm','audio/mp4':'m4a','video/mp4':'mp4','audio/mpeg':'mp3','audio/wav':'wav','audio/x-wav':'wav'}.get(mime,'bin')
        return StreamingResponse(chunks,media_type='application/octet-stream',headers={
            'Content-Disposition':f'attachment; filename="recording.{extension}"',
            'Content-Length':str(record['size'])})

    @app.get('/{path:path}')
    def static(path: str):
        entry = assets.get('/'+path)
        if not entry:
            raise HTTPException(404,'页面不存在')
        return FileResponse(HERE/'static'/entry[0],media_type=entry[1])
    return app


def from_env():
    return create_app(preview=os.getenv('SCRIBELET_LOCAL_PREVIEW')=='1')
