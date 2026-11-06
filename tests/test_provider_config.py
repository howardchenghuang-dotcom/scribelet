"""Any OpenAI-compatible provider can be configured without touching code."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import processing  # noqa: E402


class FakeStore:
    def reserve(self, **_):
        return 'today'

    def release_tokens(self, *_):
        pass


def capture(env):
    """Run one transcription and one note call; return what was sent where."""
    seen = []

    def transport(request):
        body = request.content
        seen.append((str(request.url), request.headers.get('authorization'), body))
        if request.url.path.endswith('/audio/transcriptions'):
            return httpx.Response(200, json={'text': 'hello'})
        return httpx.Response(200, json={'choices': [{'message': {'content': 'note'}, 'finish_reason': 'stop'}],
                                         'usage': {'total_tokens': 10}})

    clean = {k: v for k, v in os.environ.items() if not k.startswith('SCRIBELET_') and k != 'SILICONFLOW_API_KEY'}
    with patch.dict(os.environ, {**clean, **env}, clear=True):
        provider = processing.Provider(FakeStore())
        base = provider.client.base_url
        provider.client = httpx.Client(base_url=base, headers=provider.client.headers,
                                       transport=httpx.MockTransport(transport))
        with tempfile.TemporaryDirectory() as folder:
            audio = Path(folder) / 'a.wav'
            audio.write_bytes(b'RIFF')
            provider.transcribe(audio, 1)
        provider.summarize('prompt')
    return seen


class ProviderConfigTests(unittest.TestCase):
    def test_custom_endpoint_key_and_models_are_used(self):
        seen = capture({'SCRIBELET_API_BASE': 'https://llm.example.test/v1/', 'SCRIBELET_API_KEY': 'k-custom',
                        'SCRIBELET_ASR_MODEL': 'whisper-1', 'SCRIBELET_MODEL': 'gpt-test'})
        (asr_url, asr_auth, asr_body), (chat_url, chat_auth, chat_body) = seen
        self.assertEqual(asr_url, 'https://llm.example.test/v1/audio/transcriptions')
        self.assertEqual(chat_url, 'https://llm.example.test/v1/chat/completions')
        self.assertEqual({asr_auth, chat_auth}, {'Bearer k-custom'})
        self.assertIn(b'whisper-1', asr_body)
        chat = json.loads(chat_body)
        self.assertEqual(chat['model'], 'gpt-test')
        # Unknown fields break strict providers: none are sent to a non-default endpoint.
        self.assertNotIn('enable_thinking', chat)

    def test_default_endpoint_keeps_thinking_off(self):
        chat = json.loads(capture({'SCRIBELET_API_KEY': 'k'})[1][2])
        self.assertIs(chat['enable_thinking'], False)

    def test_extra_body_is_merged_but_cannot_replace_the_prompt(self):
        chat = json.loads(capture({'SCRIBELET_API_BASE': 'https://llm.example.test/v1', 'SCRIBELET_API_KEY': 'k',
                                   'SCRIBELET_EXTRA_BODY': '{"top_p": 0.5, "messages": []}'})[1][2])
        self.assertEqual(chat['top_p'], 0.5)
        self.assertEqual(chat['messages'][0]['content'], 'prompt')

    def test_extra_body_must_be_an_object(self):
        with patch.dict(os.environ, {'SCRIBELET_EXTRA_BODY': '[1]'}):
            with self.assertRaises(ValueError):
                processing.extra_body()

    def test_missing_key_is_refused_before_any_call(self):
        clean = {k: v for k, v in os.environ.items() if not k.startswith('SCRIBELET_') and k != 'SILICONFLOW_API_KEY'}
        with patch.dict(os.environ, clean, clear=True):
            with self.assertRaises(ValueError):
                processing.Provider(FakeStore())


if __name__ == '__main__':
    unittest.main()
