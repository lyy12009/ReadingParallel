"""Real AppTest execution; JSON is a fixed offline fixture, not LLM output."""
import hashlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import jieba
import streamlit as st
from docx import Document
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).absolute().parents[1]


class IntegrationTests(unittest.TestCase):
    def test_dictionary_and_curriculum(self):
        self.assertTrue(callable(jieba.Tokenizer))
        tokenizer = jieba.Tokenizer(dictionary=str(ROOT / 'dict.txt.big'))
        tokenizer.initialize()
        tokens = tokenizer.lcut('今天我們在臺灣學中文。', HMM=False)
        self.assertEqual(''.join(tokens), '今天我們在臺灣學中文。')
        self.assertIn('今天', tokens)
        data = json.loads((ROOT / 'corpus.json').read_text(encoding='utf-8'))
        self.assertEqual(len(data), 45)
        self.assertTrue(all(isinstance(x['對話1'], str) and x['對話1'] for x in data))

    def test_full_offline_workflow_and_word(self):
        protected = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                     for name in ('corpus.json', 'dict.txt.big')}
        outputs = {}
        original_download = st.download_button

        def capture_download(*args, **kwargs):
            outputs[kwargs['file_name']] = kwargs['data']
            return original_download(*args, **kwargs)

        def healthy(app):
            self.assertEqual([e.message for e in app.exception], [])

        body = '明華：今天上課。\n月美：你好。'
        fixture = {
            'title': '離線整合測試教材',
            'reading_texts': [{'title': '', 'content': body}],
            'supplementary_vocabulary': [
                {'word': '上課', 'source': ['reading'],
                 'explanation': {'English（英語）': 'attend class'}}],
            'instructions': '請選出正確答案。',
            'questions': [
                {'type': 'single_choice', 'stem': stem,
                 'options': ['上課', '喝茶', '喝咖啡', '回家'], 'answer': 'A'}
                for stem in ['明華今天做什麼？', '誰說今天上課？', '月美說了什麼？']],
            'validation_warnings': []}
        with patch.object(st, 'download_button', side_effect=capture_download):
            app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=90).run()
            healthy(app)
            self.assertTrue(any('繁體中文大字典已載入' in s.value for s in app.success))
            app.text_area(key='draft').set_value(body)
            next(b for b in app.button if b.label == '執行 Python 檢測').click().run()
            healthy(app)
            report = app.session_state['last_audit']
            self.assertGreater(report['全文中文字詞次'], 0)
            self.assertEqual(report['未核准說話者'], [])
            self.assertGreater(report['目標詞複現']['你好'], 0)
            approve = next(b for b in app.button if b.label == '將目前檢測內容確認為定稿')
            self.assertFalse(approve.disabled)
            approve.click().run()
            healthy(app)
            self.assertTrue(app.session_state['approved'])
            self.assertEqual(app.session_state['final_text'], body)
            self.assertTrue(any('【定稿閱讀文本：必須原樣保留】' in c.value
                                and body in c.value and 'single_choice' in c.value for c in app.code))
            app.text_area(key='v2_raw_json').set_value(json.dumps(fixture, ensure_ascii=False))
            next(b for b in app.button if b.label == '解析 JSON 並填入以下編輯區').click().run()
            healthy(app)
            self.assertTrue(app.session_state['v2_loaded'])
            self.assertEqual(app.session_state['v2_body'], body)
            self.assertEqual(app.session_state['v22_json_structure_issues'], [])
            self.assertEqual(app.session_state['v22_json_vocab_issues'], [])
            self.assertIn('1. A', app.session_state['v2_answers'])
            self.assertEqual(len(outputs), 2)

        result_dir = ROOT / 'test-results'
        result_dir.mkdir(exist_ok=True)
        for name, content in outputs.items():
            self.assertIsInstance(content, bytes)
            doc = Document(io.BytesIO(content))
            text = '\n'.join([p.text for p in doc.paragraphs] +
                             [cell.text for table in doc.tables for row in table.rows for cell in row.cells])
            for expected in ['離線整合測試教材', '明華：今天上課。', '月美：你好。',
                             'attend class', '明華今天做什麼？', 'A. 上課', 'D. 回家']:
                self.assertIn(expected, text)
            self.assertAlmostEqual(doc.sections[0].page_width.cm, 21, places=1)
            if '教師版' in name:
                self.assertIn('教師答案', text)
                self.assertIn('1. A', text)
                self.assertIn('3. A', text)
            else:
                self.assertNotIn('教師答案', text)
                self.assertNotIn('1. A', text)
            (result_dir / name).write_bytes(content)
        for name, digest in protected.items():
            self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), digest)


if __name__ == '__main__':
    unittest.main()
