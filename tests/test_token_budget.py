"""The daily token budget must let the longest allowed recording finish."""
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import Store
from processing import Provider, compose_note

SEGMENTS=14400//45          # a 4-hour upload, cut every ~45 s


def lecture(chars_per_segment):
    return {i:'[%02d:%02d] '%(i*45//60%100,i*45%60)+('so the lecture continues here '*40)[:chars_per_segment]
            for i in range(SEGMENTS)}


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=Store(self.temp.name)
        self.calls=[]

    def tearDown(self):
        self.temp.cleanup()

    def provider(self, usage=None, translate=True):
        def transport(request):
            prompt=json.loads(request.content)['messages'][0]['content']
            self.calls.append(prompt)
            if '<segments>' in prompt:
                if not translate:
                    return httpx.Response(429,json={})
                text='\n'.join('[%s] 中文译文'%i for i in re.findall(r'^\[(\d+)\]',prompt,re.M))
            else:
                text=('Concept 概念 — explanation 解释。'*400)[:8000]
            body={'choices':[{'finish_reason':'stop','message':{'content':text}}]}
            if usage is not None:
                body['usage']={'total_tokens':usage}
            return httpx.Response(200,json=body)
        provider=Provider(self.store)
        provider.client.close()
        provider.client=httpx.Client(base_url='https://api.siliconflow.cn/v1',transport=httpx.MockTransport(transport))
        return provider

    def tokens(self):
        with self.store.db() as db:
            return db.execute('SELECT COALESCE(SUM(tokens),0) FROM usage').fetchone()[0]

    def test_default_budget_finishes_a_fast_4_hour_lecture_even_without_usage_reports(self):
        # Worst case: no refunds, fast speaker, every summary batch near max_tokens.
        env={k:v for k,v in os.environ.items() if k!='SCRIBELET_DAILY_TOKENS'}
        env['SILICONFLOW_API_KEY']='test-only'
        with patch.dict(os.environ,env,clear=True):
            provider=self.provider()
            note=compose_note(provider,'Long lecture',lecture(800))
            provider.close()
        self.assertNotIn('没有生成中文对照',note,'翻译阶段不能因为当日额度用完而缺中文')
        self.assertIn('（中英对照）',note)

    def test_reservation_is_settled_to_the_billed_usage(self):
        with patch.dict(os.environ,{'SILICONFLOW_API_KEY':'test-only'}):
            provider=self.provider(usage=1234)
            provider.summarize('English 中文')
            provider.close()
        self.assertEqual(self.tokens(),1234)

    def test_missing_or_bogus_usage_keeps_the_conservative_reservation(self):
        for usage in (None,'1234',-5,10**9):
            with self.subTest(usage=usage), patch.dict(os.environ,{'SILICONFLOW_API_KEY':'test-only'}):
                before=self.tokens()
                provider=self.provider(usage=usage)
                provider.summarize('English 中文')
                provider.close()
                self.assertEqual(self.tokens()-before,len('English 中文'.encode())+8192+256)

    def test_failed_translation_is_stated_in_the_note_not_silently_dropped(self):
        with patch.dict(os.environ,{'SILICONFLOW_API_KEY':'test-only'}):
            provider=self.provider(translate=False)
            note=compose_note(provider,'Ethics',{0:'[00:00] Ethics concerns values.',1:'[00:45] Duty matters.'})
            provider.close()
        self.assertIn('2 / 2 段没有生成中文对照（模型服务暂时限流）',note)
        self.assertIn('## Transcript 原始转写\n',note)


if __name__=='__main__':
    unittest.main()
