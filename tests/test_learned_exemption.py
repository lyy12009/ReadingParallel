"""Synthetic lexical evidence is separate from real curriculum/Jieba/AppTest cases."""
import ast
from collections import Counter
from difflib import SequenceMatcher
import importlib
import json
from pathlib import Path
import re
import unittest

import jieba
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).absolute().parents[1]


def pick(app, group, label):
    return next(w for w in getattr(app, group) if w.label == label)


class LearnedRuleTests(unittest.TestCase):
    def decide(self, word, known=(), text=(), sources=None):
        return importlib.import_module('learned_vocabulary').classify_learned_word(
            word, set(known), set(text), sources)

    def test_A_B_whole_word_priority_and_no_exemption_record(self):
        for known, text, source in [({'火車'}, {'火車'}, '教材生詞'),
                                    (set(), {'火車'}, '已學課文斷詞')]:
            with self.subTest(source=source):
                result = self.decide('火車', known, text)
                self.assertEqual(result['判定來源'], source)
                self.assertEqual(result['單字證據'], [])

    def test_C_D_E_exact_single_tokens_in_either_curriculum_set(self):
        for known, text in [({'火', '車'}, set()), ({'火'}, {'車'}), (set(), {'火', '車'})]:
            with self.subTest(known=known, text=text):
                result = self.decide('火車', known, text)
                self.assertEqual(result['判定來源'], '已學單字組合豁免')
                self.assertEqual([e['單字詞'] for e in result['單字證據']], ['火', '車'])
                for evidence in result['單字證據']:
                    self.assertTrue(evidence['證據類別'])
                    self.assertIsNone(evidence['首次來源'])

    def test_F_G_H_missing_single_evidence_and_substrings_do_not_exempt(self):
        for known, text in [({'火'}, set()), (set(), set()), ({'汽車', '人'}, set()),
                            (set(), {'汽車', '人'})]:
            with self.subTest(known=known, text=text):
                word = '汽人' if '汽車' in known | text else '火車'
                self.assertIsNone(self.decide(word, known, text))

    def test_I_non_two_han_tokens_never_recurse(self):
        for word in ['火', '火車站', '火車站牌', '火1', 'A車', '火！', '']:
            with self.subTest(word=word):
                result = self.decide(word, set(word))
                self.assertTrue(result is None or result['判定來源'] != '已學單字組合豁免')

    def test_J_generated_input_does_not_extend_sets(self):
        known, text = {'火'}, set()
        self.assertIsNone(self.decide('火車', known, text))
        self.assertEqual((known, text), ({'火'}, set()))

    def test_K_multiple_sources_are_deterministic_and_traceable(self):
        sources = {'火': {'教材生詞': {'冊': '合成冊', '課': '合成課', '來源': '對話1生詞',
                                      '資料索引': 0}},
                   '車': {'已學課文斷詞': {'冊': '合成冊', '課': '合成課', '來源': '文章1',
                                         '資料索引': 1}}}
        result = self.decide('火車', {'火', '車'}, {'火', '車'}, sources)
        self.assertTrue(all(e['證據類別'] == ['教材生詞', '已學課文斷詞']
                            for e in result['單字證據']))
        self.assertEqual(result['單字證據'][0]['首次來源']['來源'], '對話1生詞')
        self.assertEqual(result['單字證據'][1]['首次來源']['來源'], '文章1')


class RealAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tokenizer = jieba.Tokenizer(dictionary=str(ROOT / 'dict.txt.big'))
        tokenizer.initialize()
        cls.ns = {'re': re, 'Counter': Counter, 'SequenceMatcher': SequenceMatcher,
                  'TOKENIZER': tokenizer, 'CJK_WORD': re.compile(r'[\u3400-\u9fffA-Za-z0-9]+'),
                  'HAN_RE': re.compile(r'[\u3400-\u9fff]'),
                  'DATA': json.loads((ROOT / 'corpus.json').read_text(encoding='utf-8')),
                  'PARTS': ['對話1', '對話2', '文章1', '文章2'], 'BIG_DICT': ROOT / 'dict.txt.big'}
        names = {'uniq', 'words', 'cleanbody', 'count_han', 'ngram_overlap', 'normalize_text',
                 'get_lexicon', 'lesson_segmented_corpus', 'curriculum_evidence',
                 'curriculum_character_sources', 'curriculum_word_index', '_curriculum_segment',
                 'audit', 'extract_quiz_language', 'audit_quiz_language', 'audit_quiz_by_item',
                 'serialize_quiz', 'quiz_vocab_review'}
        tree = ast.parse((ROOT / 'app.py').read_text(encoding='utf-8'))
        nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
        # Execute actual function bodies; cache behavior is separately covered by AppTest.
        for node in nodes:
            node.decorator_list = []
        exec(compile(ast.Module(body=nodes, type_ignores=[]), '<actual lexical functions>', 'exec'), cls.ns)

    def run_audit(self, body, known=(), text=(), **extra):
        try:
            self.ns['classify_learned_word'] = importlib.import_module('learned_vocabulary').classify_learned_word
        except ModuleNotFoundError:
            pass
        return self.ns['audit'](body, set(known), [], '', [], set(text), **extra)

    def test_actual_jieba_unknown_count_drops_only_with_both_single_words(self):
        before = self.run_audit('火車，火車。')
        after = self.run_audit('火車，火車。', {'火', '車'})
        self.assertEqual(before['疑似超綱']['火車'], 2)
        self.assertEqual(before['疑似超綱詞次'], 2)
        self.assertEqual(after['疑似超綱詞次'], 0)
        self.assertEqual(after['已學證據分類']['已學單字組合豁免'], 2)
        self.assertEqual(len(after['已學單字組合豁免']), 1)
        self.assertEqual(after['已學單字組合豁免'][0]['原詞出現次數'], 2)

    def test_full_word_sources_count_once_before_character_fallback(self):
        result = self.run_audit('火車。', {'火車', '火', '車'}, {'火車', '火', '車'})
        self.assertEqual(result['疑似超綱詞次'], 0)
        self.assertEqual(result['已學證據分類'], Counter({'教材生詞': 1}))
        self.assertEqual(result['已學單字組合豁免'], [])

    def test_one_character_and_current_input_are_still_unknown(self):
        result = self.run_audit('火車。車。', {'火'})
        self.assertEqual(result['疑似超綱']['火車'], 1)
        self.assertEqual(result['已學單字組合豁免'], [])
        self.assertGreater(result['疑似超綱詞次'], 0)

    def test_substring_of_attested_phrase_is_not_whole_learned_evidence(self):
        result = self.run_audit('火車。', attested_phrases=('火車站',))
        self.assertEqual(result['疑似超綱']['火車'], 1)

    def test_grammar_names_allow_and_function_word_rules_remain(self):
        result = self.run_audit('火車。', grammar_evidence={'火車'})
        self.assertEqual(result['疑似超綱詞次'], 0)
        self.assertEqual(result['已學證據分類']['語法例句'], 1)
        result = self.run_audit('火車。', names={'火車'})
        self.assertEqual(result['已學證據分類']['教師確認人名'], 1)
        result = self.ns['audit']('火車。', set(), [], '', ['火車'])
        self.assertEqual(result['已學證據分類']['教師允許詞'], 1)
        self.assertEqual(self.run_audit('是。')['疑似超綱詞次'], 0)

    def test_L_quiz_instructions_stems_and_options_use_same_audit(self):
        self.run_audit('火車。')
        result = self.ns['audit_quiz_language'](
            '請選火車。\n1. 火車是什麼？\nA. 火車\nB. 汽車', {'火', '車'}, [])
        stems, options = result['題幹與說明'], result['選項']
        self.assertNotIn('火車', stems['疑似超綱'])
        self.assertNotIn('火車', options['疑似超綱'])
        self.assertGreater(stems['已學單字組合豁免'][0]['原詞出現次數'], 0)
        self.assertEqual(options['疑似超綱']['汽車'], 1)

    def test_allow_grammar_and_names_are_not_single_character_learning_evidence(self):
        for key in ['grammar_evidence', 'names']:
            with self.subTest(key=key):
                result = self.run_audit('火車。', **{key: {'火', '車'}})
                self.assertEqual(result['疑似超綱']['火車'], 1)
                self.assertEqual(result['已學單字組合豁免'], [])
        result = self.ns['audit']('火車。', set(), [], '', ['火', '車'])
        self.assertEqual(result['疑似超綱']['火車'], 1)

    def test_quiz_exemption_is_not_mislabeled_as_glossary_coverage(self):
        self.run_audit('火車。')
        rows = self.ns['quiz_vocab_review']('火車。', '1. 火車是什麼？\nA. 火車',
                   ['火車'], {'火', '車'}, [], set(), set(), [], ())
        self.assertTrue(all(row['已由補充生詞涵蓋'] == '—' for row in rows))
        self.assertTrue(any(row['已學單字組合豁免明細'] for row in rows))

    def test_classified_and_unknown_token_totals_still_balance(self):
        result = self.run_audit('火車，火車，汽車。', {'火', '車'})
        self.assertEqual(sum(result['已學證據分類'].values()) + result['疑似超綱詞次'],
                         result['全文中文字詞次'])
        self.assertEqual(result['疑似超綱']['汽車'], 1)
        self.assertEqual(result['詞彙判定紀錄'][0]['原詞出現次數'], 2)

    def test_real_curriculum_complete_single_source_and_first_occurrence(self):
        self.run_audit('人人。')
        known = self.ns['get_lexicon'](0)
        text, _, _, _ = self.ns['curriculum_evidence'](0)
        sources = self.ns['curriculum_character_sources'](0)
        result = self.run_audit('人人，人人。', known, text, character_sources=sources)
        self.assertNotIn('人人', known | text)
        self.assertEqual(result['疑似超綱詞次'], 0)
        trace = result['已學單字組合豁免'][0]
        self.assertEqual(trace['原詞'], '人人')
        self.assertEqual(trace['原詞出現次數'], 2)
        first = trace['單字證據'][0]['首次來源']
        self.assertEqual((first['冊'], first['課']), ('第一冊', self.ns['DATA'][0]['課']))
        self.assertIn(first['來源'], ['對話2生詞', '對話2'])
        self.assertEqual(self.run_audit('人手。', known, text)['疑似超綱']['人手'], 1)

    def test_real_cross_book_cutoff_changes_only_current_cumulative_evidence(self):
        for idx, expected in [(0, 1), (30, 0), (0, 1)]:
            with self.subTest(idx=idx):
                known = self.ns['get_lexicon'](idx)
                text = self.ns['curriculum_evidence'](idx)[0]
                result = self.run_audit('人手。', known, text)
                self.assertEqual(result['疑似超綱詞次'], expected)

    def test_provenance_tokens_exactly_match_existing_segmented_set(self):
        text, sentences = self.ns['lesson_segmented_corpus'](0, 'test')
        detailed, other, sources = self.ns['lesson_segmented_corpus'](0, 'test', include_sources=True)
        self.assertEqual((text, sentences), (detailed, other))
        self.assertTrue(set(sources) <= text)
        self.assertTrue(all(len(word) == 1 for word in sources))
        self.assertNotIn('汽', sources)


class LearnedUITests(unittest.TestCase):
    def app(self):
        app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=90).run()
        self.assertFalse(app.exception)
        return app

    def audit(self, app, body):
        app.text_area(key='draft').set_value(body)
        pick(app, 'button', '執行 Python 檢測').click().run()
        self.assertFalse(app.exception, [e.message for e in app.exception])
        return app.session_state['last_audit']

    def test_exemption_details_visible_with_real_curriculum_sources(self):
        app = self.app()
        result = self.audit(app, '人人，人人。')
        self.assertEqual(result['疑似超綱詞次'], 0)
        self.assertEqual(result['已學單字組合豁免'][0]['原詞出現次數'], 2)
        self.assertTrue(any('已學單字組合豁免' in e.label for e in app.expander))
        self.assertTrue(any('原詞' in f.value.columns for f in app.dataframe))

    def test_M_formal_target_counts_do_not_join_separate_characters(self):
        app = self.app()
        app.multiselect(key='tv_0_selected').set_value([])
        app.text_area(key='tv_0_manual').set_value('人人').run()
        first = self.audit(app, '人，人。')['逐篇複現']['rows'][0]
        self.assertEqual((first['確定出現次數'], first['可疑出現次數']), (0, 0))
        second = self.audit(app, '人人。')['逐篇複現']['rows'][0]
        self.assertEqual(second['確定出現次數'], 1)

    def test_N_cross_book_rechecks_and_preserves_draft(self):
        app = self.app()
        result = self.audit(app, '人手。')
        self.assertEqual(result['疑似超綱']['人手'], 1)
        pick(app, 'selectbox', '冊別').set_value('第三冊').run()
        self.assertNotIn('last_audit', app.session_state)
        result = self.audit(app, '人手。')
        self.assertNotIn('人手', result['疑似超綱'])
        self.assertEqual(result['已學單字組合豁免'][0]['原詞'], '人手')
        self.assertEqual(app.session_state['draft'], '人手。')

    def test_O_existing_retained_words_and_explanations_survive_exemption(self):
        app = self.app()
        self.audit(app, '人手。')
        app.multiselect(key='retained_words').set_value(['人手']).run()
        pick(app, 'button', '只載入已定稿閱讀文本（暫不命題）').click().run()
        app.text_area(key='v2_vocab').set_value('人手：教師原有解釋').run()
        pick(app, 'selectbox', '冊別').set_value('第三冊').run()
        self.audit(app, '人手。')
        self.assertIn('人手', app.multiselect(key='retained_words').value)
        self.assertEqual(app.text_area(key='v2_vocab').value, '人手：教師原有解釋')

    def test_quiz_gui_exemption_has_same_sources_and_preserves_teacher_glossary(self):
        app = self.app()
        result = self.audit(app, '人人。')
        pick(app, 'button', '將目前檢測內容確認為定稿').click().run()
        fixture = {'title': '拆字豁免測試', 'reading_texts': [{'content': '人人。'}],
                   'instructions': '人人。',
                   'supplementary_vocabulary': [{'word': '人人', 'explanation': {'English（英語）': 'everyone'}}],
                   'questions': [{'type': 'single_choice', 'stem': '人人。',
                                  'options': ['人人', '人手', '火車', '汽車'], 'answer': 'A'}]}
        app.text_area(key='v2_raw_json').set_value(json.dumps(fixture, ensure_ascii=False))
        pick(app, 'button', '解析 JSON 並填入以下編輯區').click().run()
        self.assertFalse(app.exception, [e.message for e in app.exception])
        self.assertIn('人人', app.text_area(key='v2_vocab').value)
        for frame in app.dataframe:
            if '尚未解決' in frame.value.columns:
                json.dumps(frame.value.to_dict('records'), ensure_ascii=False)
        frames = [frame.value for frame in app.dataframe if '區域' in frame.value.columns
                  and '原詞' in frame.value.columns]
        self.assertTrue(frames)
        details = frames[-1]
        self.assertTrue(all(word == '人人' for word in details['原詞']))
        self.assertIn('選項', details['區域'].tolist())
        self.assertTrue(any('教材生詞' in value for value in details['單字1證據']))
        self.assertEqual(result['已學單字組合豁免'][0]['原詞出現次數'], 1)

    def test_cancelling_saved_retained_word_is_respected_on_reaudit(self):
        app = self.app()
        self.audit(app, '人手。')
        app.multiselect(key='retained_words').set_value(['人手']).run()
        app.multiselect(key='retained_words').set_value([]).run()
        self.audit(app, '人手。')
        self.assertEqual(app.multiselect(key='retained_words').value, [])

    def test_rule_version_invalidates_old_reports_without_deleting_text(self):
        app = self.app()
        self.audit(app, '人人。')
        pick(app, 'button', '將目前檢測內容確認為定稿').click().run()
        snapshot = json.loads(app.session_state['active_target_settings'])
        snapshot.pop('vocabulary_rule', None)
        old = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
        app.session_state['active_target_settings'] = old
        app.session_state['audited_target_settings'] = old
        app.run()
        self.assertNotIn('last_audit', app.session_state)
        self.assertFalse(app.session_state['approved'])
        self.assertEqual(app.session_state['draft'], '人人。')
