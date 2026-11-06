"""Lecture and note languages: chosen per recording, carried through transcription, notes and translation."""
from html.parser import HTMLParser
import io
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

from fastapi.testclient import TestClient
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import create_app, password_hash, Store  # noqa: E402
import bilingual  # noqa: E402
import languages  # noqa: E402
import processing  # noqa: E402
from processing import compose_note, process_record  # noqa: E402

ORIGIN = 'https://testserver'
PASSWORD = 'a-test-password-only-1234'
RID = 'b' * 32


def ffmpeg():
    import shutil
    found = os.environ.get('SCRIBELET_FFMPEG') or shutil.which('ffmpeg')
    if found:
        return found
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


class Model:
    """Answers translation prompts line by line and records every prompt it saw."""
    def __init__(self, translated='traducción'):
        self.prompts, self.translated = [], translated

    def summarize(self, prompt):
        self.prompts.append(prompt)
        if '<segments>' in prompt:
            material = prompt.split('<segments>\n', 1)[1].split('\n</segments>', 1)[0]
            return '\n'.join(line.split(']', 1)[0] + '] ' + self.translated for line in material.splitlines())
        return '## Notes'


DONE = {0: '[00:00] La ética trata de valores.', 1: '[00:45] El deber importa.'}


class NoteLanguageTests(unittest.TestCase):
    def test_spanish_lecture_english_notes(self):
        model = Model('Ethics is about values.')
        note = compose_note(model, 'Ética', DONE, 'es', 'en')
        summary, translation = model.prompts
        self.assertIn('Spanish first, immediately followed by the same meaning in English', summary)
        self.assertIn('into natural, accurate English', translation)
        self.assertIn('lecture recording in Spanish', translation)
        self.assertIn('## Transcript (with translation)', note)
        self.assertIn('[00:00] La ética trata de valores.\n\nEthics is about values.', note)
        self.assertNotIn('中文', note)

    def test_same_language_makes_one_language_notes_without_translation(self):
        model = Model()
        note = compose_note(model, 'Cours', {0: '[00:00] Bonjour.'}, 'fr', 'fr')
        self.assertEqual(len(model.prompts), 1, 'no translation call when lecture and notes share a language')
        self.assertIn('entirely in French', model.prompts[0])
        self.assertIn('## Transcription\n', note)

    def test_auto_detected_lecture_asks_to_copy_segments_already_in_the_note_language(self):
        model = Model()
        compose_note(model, 'Lecture', DONE, 'auto', 'en')
        summary, translation = model.prompts
        self.assertIn("the lecture's own language first", summary)
        self.assertIn('If a segment is already in English, copy it unchanged.', translation)

    def test_failed_translation_is_reported_in_the_note_language(self):
        class Broken(Model):
            def summarize(self, prompt):
                if '<segments>' in prompt:
                    raise ValueError('模型服务暂时限流；录音已保存，可稍后重试。')
                return '## Notes'
        note = compose_note(Broken(), 'Ethik', DONE, 'en', 'de')
        self.assertIn('2 von 2 Transkript-Abschnitten haben keine Übersetzung (模型服务暂时限流)', note)

    def test_english_to_chinese_keeps_the_original_prompt(self):
        prompt = bilingual.build_summary_prompt('material', 'classic')
        self.assertTrue(prompt.startswith(bilingual.BILINGUAL_RULES))
        self.assertEqual(prompt, bilingual.build_summary_prompt('material', 'classic', 'en', 'zh'))
        self.assertIn('完整翻译成自然、准确的中文', bilingual.build_translation_prompt('[0] hi', 'en', 'zh'))

    def test_every_note_language_has_every_notice(self):
        self.assertEqual(set(languages.NOTICES), set(languages.LANGUAGES))
        for code, texts in languages.NOTICES.items():
            self.assertEqual(set(texts), {'heading_pair', 'heading', 'missing', 'quality'}, code)
            texts['missing'].format(1, 2, ' (x)')
            texts['quality'].format(10, 3)

    def test_language_boxes_offer_exactly_the_supported_languages(self):
        class Options(HTMLParser):
            def __init__(self):
                super().__init__(); self.select = None; self.found = {}
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == 'select':
                    self.select = attrs.get('id'); self.found.setdefault(self.select, [])
                elif tag == 'option' and self.select:
                    self.found[self.select].append(attrs['value'])
            def handle_endtag(self, tag):
                if tag == 'select':
                    self.select = None
        page = Options(); page.feed((ROOT / 'static/index.html').read_text(encoding='utf-8'))
        self.assertEqual(page.found['sourceLang'], ['auto'] + list(languages.LANGUAGES))
        self.assertEqual(page.found['targetLang'], list(languages.LANGUAGES))


class TranscriptionHintTests(unittest.TestCase):
    def form(self, base, language):
        sent = []

        def transport(request):
            sent.append(request.content)
            return httpx.Response(200, json={'text': 'hola'})

        class Quota:
            def reserve(self, **_): return 'today'

        env = {'SCRIBELET_API_KEY': 'k', 'SCRIBELET_API_BASE': base}
        with patch.dict(os.environ, env):
            provider = processing.Provider(Quota())
            provider.client = httpx.Client(base_url=provider.client.base_url, transport=httpx.MockTransport(transport))
            with tempfile.TemporaryDirectory() as folder:
                audio = Path(folder) / 'a.wav'; audio.write_bytes(b'RIFF')
                provider.transcribe(audio, 1, language)
        return sent[0]

    def test_whisper_style_endpoints_get_the_language_hint(self):
        self.assertIn(b'name="language"\r\n\r\nes', self.form('https://api.example.test/v1', 'es'))

    def test_no_hint_for_auto_or_the_default_endpoint(self):
        self.assertNotIn(b'name="language"', self.form('https://api.example.test/v1', 'auto'))
        self.assertNotIn(b'name="language"', self.form(processing.DEFAULT_API_BASE, 'es'))


class RecordLanguageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app(self.temp.name, ORIGIN, 'owner', password_hash(PASSWORD), start_worker=False)
        self.client = TestClient(self.app, base_url=ORIGIN)
        self.store = self.app.state.store
        self.client.post('/api/login', headers={'Origin': ORIGIN}, json={'username': 'owner', 'password': PASSWORD})
        self.headers = {'Origin': ORIGIN, 'X-CSRF-Token': self.client.get('/api/me').json()['csrf']}

    def tearDown(self):
        self.client.close(); self.temp.cleanup()

    def create(self, data=b'audio', **langs):
        return self.client.post('/api/records', headers=self.headers,
                                json={'id': RID, 'title': 'Clase', 'size': len(data), 'mime': 'audio/wav', **langs})

    def test_choice_is_stored_and_unknown_languages_are_refused(self):
        self.assertEqual(self.create(source_lang='klingon').status_code, 400)
        self.assertEqual(self.create(target_lang='auto').status_code, 400, 'notes need a concrete language')
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM records').fetchone()[0], 0)
        self.assertEqual(self.create(source_lang='es', target_lang='en').status_code, 200)
        record = self.client.get(f'/api/records/{RID}').json()
        self.assertEqual((record['source_lang'], record['target_lang']), ('es', 'en'))

    def test_missing_choice_means_english_lecture_chinese_notes(self):
        self.assertEqual(self.create().status_code, 200)
        self.assertEqual(processing.record_languages(self.store.record(RID)), ('en', 'zh'))

    def test_databases_from_before_language_choice_are_upgraded(self):
        with tempfile.TemporaryDirectory() as folder:
            db = sqlite3.connect(Path(folder) / 'scribelet.sqlite3')
            db.execute("CREATE TABLE records (id TEXT PRIMARY KEY, title TEXT, size INTEGER, mime TEXT, state TEXT, created REAL, updated REAL, "
                       "error TEXT DEFAULT '', note TEXT DEFAULT '', version INTEGER DEFAULT 0, duration REAL DEFAULT 0, deleted INTEGER DEFAULT 0, "
                       "progress INTEGER DEFAULT 0, manual TEXT DEFAULT '')")
            db.execute("INSERT INTO records(id,title,size,mime,state,created,updated) VALUES(?,?,?,?,?,?,?)", (RID, 'Old', 1, 'audio/wav', 'done', 0, 0))
            db.commit(); db.close()
            store = Store(folder)
            self.assertEqual(processing.record_languages(store.record(RID)), ('en', 'zh'))

    def test_recording_is_processed_in_its_own_languages(self):
        audio = io.BytesIO()
        with wave.open(audio, 'wb') as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000); wav.writeframes(b'\0\0' * 32000)
        data = audio.getvalue()
        self.assertEqual(self.create(data, source_lang='ja', target_lang='ko').status_code, 200)
        self.client.put(f'/api/records/{RID}/parts/0', headers=self.headers, content=data)
        seen = {'asr': [], 'prompts': []}

        class Fake(Model):
            def __init__(self, store): super().__init__('번역'); self.prompts = seen['prompts']
            def close(self): pass
            def transcribe(self, path, seconds, language=None):
                seen['asr'].append(language); return '倫理は価値に関わる。'

        with patch.dict(os.environ, {'SCRIBELET_FFMPEG': ffmpeg()}):
            process_record(self.store, RID, provider_factory=Fake)
        record = self.store.record(RID)
        self.assertEqual(record['state'], 'done')
        self.assertEqual(seen['asr'], ['ja'])
        self.assertIn('Japanese first, immediately followed by the same meaning in Korean', seen['prompts'][0])
        self.assertIn('## 전사본 (번역 포함)', record['note'])


if __name__ == '__main__':
    unittest.main()
