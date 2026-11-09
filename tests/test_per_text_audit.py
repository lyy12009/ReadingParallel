"""Per-text audit: real existing lexical pipeline plus explicit format/error cases."""
import ast
import importlib
from pathlib import Path
import re
import unittest

import jieba
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).absolute().parents[1]


def config(targets=('咖啡',), count=1, minimums=None):
    return {'targets': list(targets), 'text_count': count,
            'minimums': minimums if minimums is not None else
            {w: {p: 1 for p in range(1, count + 1)} for w in targets}}


def pick(app, group, label):
    return next(w for w in getattr(app, group) if w.label == label)


class PerTextUnitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tokenizer = jieba.Tokenizer(dictionary=str(ROOT / 'dict.txt.big'))
        cls.tokenizer.initialize()
        cls.pattern = re.compile(r'[\u3400-\u9fffA-Za-z0-9]+')
        cls.ns = {'re': re, 'TOKENIZER': cls.tokenizer, 'CJK_WORD': cls.pattern}
        tree = ast.parse((ROOT / 'app.py').read_text(encoding='utf-8'))
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef)
                 and n.name in ('cleanbody', '_curriculum_segment', 'curriculum_word_index')]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), '<actual lexical pipeline>', 'exec'),
             cls.ns)

    def run_audit(self, text, cfg=None, baseline=None, views=None):
        module = importlib.import_module('vocabulary_audit')
        index = self.ns['curriculum_word_index'](set(baseline or ()))
        def real_views(fragment):
            return (self.ns['_curriculum_segment'](fragment, index),
                    self.tokenizer.lcut(fragment, HMM=False))
        return module.audit_per_text(text, cfg or config(), views or real_views,
                                     self.ns['cleanbody'], self.pattern)

    def test_model_claim_does_not_override_counts_or_mutate_requirements(self):
        cfg = config(minimums={'咖啡': {1: 3}})
        result = self.run_audit('全部通過。', cfg, {'咖啡'})
        self.assertEqual(result['rows'][0]['確定出現次數'], 0)
        self.assertEqual(result['rows'][0]['確定不足量'], 3)
        self.assertEqual(result['rows'][0]['狀態'], '未達標')
        self.assertEqual(cfg, {'targets': ['咖啡'], 'text_count': 1,
                              'minimums': {'咖啡': {1: 3}}})

    def test_trace_offsets_remain_body_offsets_when_label_repeats_target(self):
        result = self.run_audit('咖啡：咖啡豆', baseline={'咖啡'})
        trace = result['ambiguities'][0]
        self.assertEqual((trace['起始位置'], trace['結束位置']), (3, 5))
        self.assertEqual(trace['原文'][3:5], '咖啡')

    def test_target_is_not_joined_across_punctuation(self):
        result = self.run_audit('你，好。', config(('你好',)), {'你好'})
        row = result['rows'][0]
        self.assertEqual((row['確定出現次數'], row['可疑出現次數']), (0, 0))
        self.assertEqual(row['狀態'], '未達標')

    def test_three_texts_cannot_borrow_counts_from_each_other(self):
        text = '【文本一】甲\n咖啡，咖啡，咖啡。\n【文本二】乙\n茶。\n【文本三】丙\n茶。'
        result = self.run_audit(text, config(count=3), {'咖啡', '茶'})
        self.assertEqual(result['issues'], [])
        self.assertEqual([r['確定出現次數'] for r in result['rows']], [3, 0, 0])
        self.assertEqual([r['狀態'] for r in result['rows']], ['達標', '未達標', '未達標'])

    def test_coverage_once_is_not_minimum_twice(self):
        cfg = config(('咖啡', '茶'), minimums={'咖啡': {1: 2}, '茶': {1: 2}})
        result = self.run_audit('咖啡，茶。', cfg, {'咖啡', '茶'})
        self.assertEqual([r['確定出現次數'] for r in result['rows']], [1, 1])
        self.assertEqual([r['確定不足量'] for r in result['rows']], [1, 1])
        self.assertTrue(all(r['狀態'] == '未達標' for r in result['rows']))

    def test_compound_inner_match_has_trace_but_no_certain_count(self):
        result = self.run_audit('明華：咖啡豆。', baseline={'咖啡'})
        row = result['rows'][0]
        self.assertEqual((row['確定出現次數'], row['可疑出現次數'], row['狀態']), (0, 1, '待核對'))
        trace = result['ambiguities'][0]
        self.assertEqual(trace['行號'], 1)
        self.assertEqual(trace['原文'][trace['起始位置']:trace['結束位置']], '咖啡')
        self.assertIn('咖啡豆', trace['Jieba斷詞'])
        self.assertIn('咖啡', trace['教材優先斷詞'])

    def test_one_certain_plus_one_suspect_is_not_two_certain(self):
        result = self.run_audit('咖啡，咖啡豆。', config(minimums={'咖啡': {1: 2}}), {'咖啡'})
        row = result['rows'][0]
        self.assertEqual((row['確定出現次數'], row['可疑出現次數'], row['確定不足量']), (1, 1, 1))
        self.assertEqual(row['狀態'], '待核對')

    def test_split_manual_target_is_not_forced_into_tokenizer(self):
        result = self.run_audit('台灣。', config(('台灣',)))
        self.assertEqual(result['rows'][0]['確定出現次數'], 0)
        self.assertEqual(result['rows'][0]['可疑出現次數'], 1)
        self.assertEqual(result['rows'][0]['狀態'], '待核對')

    def test_unknown_full_token_does_not_mutate_curriculum(self):
        baseline = set()
        result = self.run_audit('咖啡。', baseline=baseline)
        self.assertEqual(result['rows'][0]['確定出現次數'], 1)
        self.assertEqual(baseline, set())

    def test_heading_and_speaker_labels_are_not_body_occurrences(self):
        result = self.run_audit('【文本一】咖啡標題\n咖啡：茶。', baseline={'咖啡', '茶'})
        self.assertEqual(result['rows'][0]['確定出現次數'], 0)
        self.assertEqual(result['rows'][0]['可疑出現次數'], 0)

    def test_missing_piece_is_unknown_not_automatically_passed(self):
        result = self.run_audit('【文本一】甲\n咖啡。\n【文本三】丙\n茶。',
                                config(count=3), {'咖啡', '茶'})
        self.assertTrue(result['issues'])
        rows = result['rows']
        self.assertEqual(rows[0]['狀態'], '達標')
        self.assertIsNone(rows[1]['確定出現次數'])
        self.assertIsNone(rows[1]['確定不足量'])
        self.assertEqual(rows[1]['狀態'], '待核對')
        self.assertEqual(rows[2]['狀態'], '未達標')

    def test_unlabelled_multiple_texts_are_not_assigned_by_blank_lines(self):
        result = self.run_audit('咖啡。\n\n茶。', config(count=2), {'咖啡', '茶'})
        self.assertTrue(result['issues'])
        self.assertTrue(all(r['確定出現次數'] is None and r['狀態'] == '待核對'
                            for r in result['rows']))

    def test_ambiguous_structure_is_not_guessed(self):
        texts = [
            '【文本一】甲\n咖啡。\n【文本一】乙\n咖啡。',
            '【文本二】乙\n咖啡。\n【文本一】甲\n咖啡。',
            '【文本甲】甲\n咖啡。\n【文本二】乙\n咖啡。',
            '未歸屬正文\n【文本一】甲\n咖啡。\n【文本二】乙\n咖啡。',
            '【文本一】甲\n咖啡。\n【文本二】乙\n咖啡。\n【文本三】丙\n咖啡。',
        ]
        for text in texts:
            with self.subTest(text=text):
                result = self.run_audit(text, config(count=2), {'咖啡'})
                self.assertTrue(result['issues'])
                self.assertTrue(all(r['狀態'] == '待核對' for r in result['rows']))

    def test_supported_headers_and_single_text_compatibility(self):
        for text in ['咖啡。', '【文本一】甲\n咖啡。', '【文本1】甲\n咖啡。',
                     '## 文本一：甲\n咖啡。']:
            with self.subTest(text=text):
                result = self.run_audit(text, baseline={'咖啡'})
                self.assertEqual(result['issues'], [])
                self.assertEqual(result['rows'][0]['確定出現次數'], 1)

    def test_missing_minimum_is_not_silently_defaulted(self):
        result = self.run_audit('咖啡。', config(minimums={'咖啡': {}}), {'咖啡'})
        self.assertTrue(result['issues'])
        self.assertEqual(result['rows'][0]['狀態'], '待核對')
        self.assertIsNone(result['rows'][0]['最低次數'])

    def test_punctuated_target_requires_review_not_substring_count(self):
        result = self.run_audit('咖啡！', config(('咖啡！',)))
        self.assertTrue(result['issues'])
        self.assertEqual(result['rows'][0]['狀態'], '待核對')

    def test_empty_input_or_title_only_is_not_a_body(self):
        for text in ['', ' \n ', '【文本一】咖啡標題']:
            with self.subTest(text=text):
                result = self.run_audit(text)
                self.assertTrue(result['issues'])
                self.assertIsNone(result['rows'][0]['確定出現次數'])
                self.assertEqual(result['rows'][0]['狀態'], '待核對')

    def test_misaligned_token_stream_is_reported(self):
        result = self.run_audit('咖啡。', views=lambda fragment: (['錯誤內容'], ['咖啡']))
        self.assertTrue(result['issues'])
        self.assertEqual(result['rows'][0]['狀態'], '待核對')

    def test_suspect_does_not_cancel_an_independently_certain_minimum(self):
        result = self.run_audit('咖啡，咖啡豆。', baseline={'咖啡'})
        self.assertEqual(result['rows'][0]['確定出現次數'], 1)
        self.assertEqual(result['rows'][0]['可疑出現次數'], 1)
        self.assertEqual(result['rows'][0]['狀態'], '達標')


class PerTextUITests(unittest.TestCase):
    def app(self, count=1):
        app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=60).run()
        self.healthy(app)
        app.multiselect(key='tv_0_selected').set_value(['咖啡'])
        pick(app, 'selectbox', '生成篇數').set_value(count).run()
        self.healthy(app)
        return app

    def healthy(self, app):
        self.assertEqual([e.message for e in app.exception], [])

    def audit(self, app, text):
        app.text_area(key='draft').set_value(text)
        pick(app, 'button', '執行 Python 檢測').click().run()
        self.healthy(app)
        return app.session_state['last_audit']['逐篇複現']

    def test_supported_plain_headers_are_not_speaker_names(self):
        app = self.app(2)
        result = self.audit(app, '文本一：甲\n咖啡。\n文本二：乙\n咖啡。')
        self.assertTrue(all(r['狀態'] == '達標' for r in result['rows']))
        self.assertEqual(app.session_state['last_audit']['未核准說話者'], [])
        self.assertFalse(pick(app, 'button', '將目前檢測內容確認為定稿').disabled)

    def test_body_unknown_speakers_are_still_reported(self):
        app = self.app()
        self.audit(app, '【文本一】甲\n未知者：咖啡。')
        self.assertIn('未知者', app.session_state['last_audit']['未核准說話者'])
        self.assertTrue(pick(app, 'button', '將目前檢測內容確認為定稿').disabled)

    def test_each_target_and_piece_uses_its_current_minimum(self):
        app = self.app(3)
        app.multiselect(key='tv_0_selected').set_value(['咖啡', '茶']).run()
        app.number_input(key='tv_0_min_咖啡_1').set_value(2)
        app.number_input(key='tv_0_min_咖啡_3').set_value(3)
        app.number_input(key='tv_0_min_茶_2').set_value(2)
        app.run()
        result = self.audit(app, '【文本一】甲\n咖啡，茶。\n【文本二】乙\n咖啡，茶，茶。\n【文本三】丙\n咖啡，茶。')
        self.assertEqual([(r['目標詞'], r['篇章'], r['最低次數'], r['確定出現次數'],
                           r['確定不足量'], r['狀態']) for r in result['rows']],
                         [('咖啡', 1, 2, 1, 1, '未達標'), ('咖啡', 2, 1, 1, 0, '達標'),
                          ('咖啡', 3, 3, 1, 2, '未達標'), ('茶', 1, 1, 1, 0, '達標'),
                          ('茶', 2, 2, 2, 0, '達標'), ('茶', 3, 1, 1, 0, '達標')])

    def test_table_has_current_per_piece_numbers(self):
        app = self.app(3)
        result = self.audit(app, '【文本一】甲\n咖啡，咖啡，咖啡。\n【文本二】乙\n茶。\n【文本三】丙\n茶。')
        self.assertEqual([r['確定出現次數'] for r in result['rows']], [3, 0, 0])
        self.assertEqual([r['狀態'] for r in result['rows']], ['達標', '未達標', '未達標'])
        columns = {'目標詞', '篇章', '最低次數', '確定出現次數', '可疑出現次數', '確定不足量', '狀態'}
        self.assertTrue(any(columns <= set(frame.value.columns) for frame in app.dataframe))
        self.assertTrue(any('舊版彙總' in m.label for m in app.metric))

    def test_missing_format_warns_without_deleting_input(self):
        app = self.app(2)
        body = '咖啡。\n\n茶。'
        result = self.audit(app, body)
        self.assertTrue(result['issues'])
        self.assertTrue(all(r['狀態'] == '待核對' for r in result['rows']))
        self.assertEqual(app.session_state['draft'], body)
        self.assertTrue(any('分篇' in w.value for w in app.warning))

    def test_editing_text_invalidates_and_reaudit_keeps_exact_input(self):
        app = self.app()
        self.audit(app, '咖啡。')
        pick(app, 'button', '將目前檢測內容確認為定稿').click().run()
        self.assertTrue(app.session_state['approved'])
        app.text_area(key='draft').set_value('茶。').run()
        self.healthy(app)
        self.assertNotIn('last_audit', app.session_state)
        self.assertFalse(app.session_state['approved'])
        self.assertEqual(app.session_state['final_text'], '咖啡。')
        new_body = '  咖啡，咖啡。\n'
        result = self.audit(app, new_body)
        self.assertEqual(result['rows'][0]['確定出現次數'], 2)
        self.assertEqual(app.session_state['audited_text'], new_body)
        self.assertFalse(pick(app, 'button', '將目前檢測內容確認為定稿').disabled)

    def test_minimum_change_clears_formal_report_and_rechecks(self):
        app = self.app()
        self.audit(app, '咖啡。')
        app.number_input(key='tv_0_min_咖啡_1').set_value(2).run()
        self.assertNotIn('last_audit', app.session_state)
        self.assertEqual(app.session_state['draft'], '咖啡。')
        result = self.audit(app, app.session_state['draft'])
        row = result['rows'][0]
        self.assertEqual((row['最低次數'], row['確定出現次數'], row['確定不足量']), (2, 1, 1))
        self.assertEqual(row['狀態'], '未達標')

    def test_target_reselection_uses_only_current_targets(self):
        app = self.app()
        self.audit(app, '咖啡，茶。')
        app.multiselect(key='tv_0_selected').set_value(['茶']).run()
        self.assertNotIn('last_audit', app.session_state)
        result = self.audit(app, '咖啡，茶。')
        self.assertEqual([(r['目標詞'], r['確定出現次數']) for r in result['rows']], [('茶', 1)])

    def test_real_compound_trace_is_displayed(self):
        app = self.app()
        app.number_input(key='tv_0_min_咖啡_1').set_value(2).run()
        result = self.audit(app, '咖啡，咖啡豆。')
        self.assertEqual(result['rows'][0]['狀態'], '待核對')
        self.assertEqual(result['rows'][0]['可疑出現次數'], 1)
        self.assertTrue(any('Jieba斷詞' in frame.value.columns for frame in app.dataframe))

    def test_allowlist_changes_invalidate_lexical_evidence(self):
        app = self.app()
        self.audit(app, '咖啡豆。')
        next(w for w in app.text_area if w.label.startswith('教師允許保留的額外詞')).set_value('咖啡豆').run()
        self.assertNotIn('last_audit', app.session_state)
        self.assertFalse(app.session_state['approved'])
        self.assertEqual(app.session_state['draft'], '咖啡豆。')


if __name__ == '__main__':
    unittest.main()
