"""Constraint summaries use real arithmetic; warning-policy mocks are explicit."""
import copy
import importlib
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).absolute().parents[1]
PATTERN = re.compile(r'[\u3400-\u9fffA-Za-z0-9]+')


def config(targets=('咖啡', '茶'), count=2, minimums=None):
    return {'lesson_id': 0, 'targets': list(targets), 'text_count': count,
            'minimums': minimums if minimums is not None else
            {word: {piece: 3 for piece in range(1, count+1)} for word in targets}}


def context():
    return {'book': '第一冊', 'lesson': '第一課', 'reference': '對話1',
            'names': ['月美'], 'grammar': ['問句：嗎'], 'guides': ['朋友'],
            'allowed': ['銀行'], 'form': '對話', 'relation': '各篇獨立',
            'theme': '校園', 'source': '參考課文', 'legacy_cover': '自然度優先'}


def pick(app, group, label):
    return next(w for w in getattr(app, group) if w.label == label)


class ConstraintUnitTests(unittest.TestCase):
    def run_summary(self, cfg=None, length=(100, 150), ctx=None):
        module = importlib.import_module('generation_constraints')
        return module.summarize_constraints(cfg if cfg is not None else config(),
                                            length, ctx if ctx is not None else context(), PATTERN)

    def test_per_piece_minimums_are_independent(self):
        cfg = config(minimums={'咖啡': {1: 2, 2: 4}, '茶': {1: 1, 2: 3}})
        result = self.run_summary(cfg)
        self.assertEqual([r['最低總詞次'] for r in result['rows']], [3, 7])
        self.assertEqual(result['minimums'], [
            {'篇章': 1, '目標詞': '咖啡', '最低次數': 2},
            {'篇章': 1, '目標詞': '茶', '最低次數': 1},
            {'篇章': 2, '目標詞': '咖啡', '最低次數': 4},
            {'篇章': 2, '目標詞': '茶', '最低次數': 3}])
        self.assertEqual(result['issues'], [])

    def test_five_targets_repeated_three_times_are_fifteen_not_five(self):
        result = self.run_summary(config(('咖啡', '茶', '你好', '喝', '請問'), count=1))
        self.assertEqual(result['rows'][0]['目標詞數量'], 5)
        self.assertEqual(result['rows'][0]['最低總詞次'], 15)

    def test_empty_targets_are_zero_without_claiming_feasibility(self):
        result = self.run_summary(config((), count=2))
        self.assertEqual([r['目標詞數量'] for r in result['rows']], [0, 0])
        self.assertEqual([r['最低總詞次'] for r in result['rows']], [0, 0])
        self.assertEqual(result['minimums'], [])
        self.assertTrue(result['unassessed'])
        self.assertNotIn('feasible', result)

    def test_inputs_and_nested_context_are_not_modified_or_aliased(self):
        cfg, ctx = config(), context()
        before = copy.deepcopy((cfg, ctx))
        result = self.run_summary(cfg, ctx=ctx)
        self.assertEqual((cfg, ctx), before)
        self.assertEqual(result['context'], ctx)
        result['context']['names'].append('另一人')
        self.assertEqual((cfg, ctx), before)

    def test_length_bounds_are_reported_without_density_thresholds(self):
        for length in [(1, 2), (100, 150), (400, 500)]:
            with self.subTest(length=length):
                result = self.run_summary(length=length)
                self.assertEqual([(r['篇幅下限'], r['篇幅上限']) for r in result['rows']],
                                 [length, length])
                self.assertEqual(result['issues'], [])
                self.assertTrue(all(r['類別'] == 'B' for r in result['risks']))
                self.assertTrue(all('待教師評估' in r['說明'] for r in result['risks']))

    def test_extreme_but_valid_settings_do_not_invent_certain_conflict(self):
        cfg = config(count=1, minimums={'咖啡': {1: 10000}, '茶': {1: 10000}})
        result = self.run_summary(cfg, (1, 2))
        self.assertEqual(result['rows'][0]['最低總詞次'], 20000)
        self.assertEqual(result['issues'], [])
        self.assertTrue(any('判準' in r['說明'] for r in result['risks']))
        self.assertNotIn('不可能', str(result))

    def test_missing_or_invalid_minimum_is_unknown_not_zero(self):
        for value in [None, 0, -1, True, '3', 1.5]:
            with self.subTest(value=value):
                cfg = config(minimums={'咖啡': {1: value, 2: 2}, '茶': {1: 1, 2: 1}})
                result = self.run_summary(cfg)
                self.assertIsNone(result['rows'][0]['最低總詞次'])
                self.assertEqual(result['rows'][1]['最低總詞次'], 3)
                self.assertTrue(any(i['篇章'] == 1 and i['類別'] == 'A'
                                    and '咖啡' in i['依據'] for i in result['issues']))

    def test_invalid_length_range_is_a_format_issue_not_teaching_risk(self):
        for length in [(150, 100), (0, 100), ('100', 150), (True, 2), ()]:
            with self.subTest(length=length):
                result = self.run_summary(length=length)
                self.assertTrue(any(i['類別'] == 'A' and '篇幅' in i['依據']
                                    for i in result['issues']))
                self.assertTrue(all(r['篇幅下限'] is None for r in result['rows']))

    def test_unsupported_target_uses_existing_fragment_format(self):
        cfg = config(('咖啡！',), count=1)
        result = self.run_summary(cfg)
        self.assertTrue(any(i['篇章'] == 1 and '咖啡！' in i['依據']
                            for i in result['issues']))
        self.assertEqual(cfg['targets'], ['咖啡！'])
        self.assertEqual(result['rows'][0]['最低總詞次'], 3)

    def test_duplicate_targets_count_distinct_words_without_mutating_source(self):
        cfg = config(('咖啡', '咖啡'), count=1)
        result = self.run_summary(cfg)
        self.assertEqual(result['rows'][0]['目標詞數量'], 1)
        self.assertEqual(result['rows'][0]['最低總詞次'], 3)
        self.assertTrue(result['issues'])
        self.assertEqual(cfg['targets'], ['咖啡', '咖啡'])

    def test_invalid_text_count_does_not_invent_pieces(self):
        for count in [0, -1, True, '2', None]:
            with self.subTest(count=count):
                cfg = {'targets': ['咖啡'], 'text_count': count, 'minimums': {}}
                result = self.run_summary(cfg)
                self.assertEqual(result['rows'], [])
                self.assertTrue(result['issues'])

    def test_context_never_becomes_extra_recurrence_targets(self):
        result = self.run_summary(config(('咖啡',), count=1))
        self.assertEqual([r['目標詞'] for r in result['minimums']], ['咖啡'])
        for field in ['names', 'grammar', 'guides', 'allowed', 'source', 'relation', 'theme']:
            self.assertEqual(result['context'][field], context()[field])
        self.assertTrue(any('無法自動判定' in text for text in result['unassessed']))


    def test_malformed_targets_and_matrix_report_unknown_without_repair(self):
        for targets in [None, '咖啡', ['咖啡', None], [' ']]:
            with self.subTest(targets=targets):
                cfg = {'text_count': 1, 'targets': targets, 'minimums': {}}
                before = copy.deepcopy(cfg)
                result = self.run_summary(cfg)
                self.assertIsNone(result['rows'][0]['目標詞數量'])
                self.assertIsNone(result['rows'][0]['最低總詞次'])
                self.assertTrue(result['issues'])
                self.assertEqual(cfg, before)
        result = self.run_summary({'text_count': 1, 'targets': ['咖啡'], 'minimums': None})
        self.assertIsNone(result['rows'][0]['最低總詞次'])

    def test_inactive_saved_values_do_not_leak_into_active_piece_totals(self):
        cfg = config(('咖啡',), count=1, minimums={'咖啡': {1: 2, 2: 99}, '茶': {1: 88}})
        result = self.run_summary(cfg)
        self.assertEqual(result['rows'][0]['最低總詞次'], 2)
        self.assertEqual(result['minimums'], [{'篇章': 1, '目標詞': '咖啡', '最低次數': 2}])

    def test_trace_inputs_preserve_exact_current_matrix_and_length(self):
        cfg, ctx = config(), context()
        result = self.run_summary(cfg, (200, 300), ctx)
        self.assertEqual(result['inputs'], {'target_config': cfg, 'length': (200, 300)})
        result['inputs']['target_config']['minimums']['咖啡'][1] = 99
        self.assertEqual(cfg['minimums']['咖啡'][1], 3)


class ConstraintUITests(unittest.TestCase):
    def app(self, count=1):
        app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=90).run()
        app.multiselect(key='tv_0_selected').set_value(['咖啡', '茶'])
        pick(app, 'selectbox', '生成篇數').set_value(count).run()
        self.healthy(app)
        return app

    def healthy(self, app):
        self.assertEqual([e.message for e in app.exception], [])

    def summary(self, app):
        columns = {'篇章', '目標詞數量', '最低總詞次', '篇幅下限', '篇幅上限'}
        return next(frame.value for frame in app.dataframe if columns <= set(frame.value.columns))

    def generation(self, app):
        return next(c.value for c in app.code if '【生成條件】' in c.value)

    def test_summary_appears_before_generation_prompt(self):
        app = self.app(2)
        frame = self.summary(app)
        self.assertEqual(frame['最低總詞次'].tolist(), [2, 2])
        self.assertTrue(any('生成條件摘要與提醒' in e.value for e in app.markdown))
        self.assertTrue(self.generation(app))
        # Main block ordering is checked by the actual element tree.
        nodes = list(app.main)
        summary_position = next(i for i, node in enumerate(nodes)
                                if getattr(node, 'type', '') == 'markdown' and
                                node.value == '生成條件摘要與提醒（不阻擋）')
        prompt_position = next(i for i, node in enumerate(nodes)
                               if getattr(node, 'type', '') == 'expander' and
                               getattr(node, 'label', '') == '生成提示詞')
        self.assertLess(summary_position, prompt_position)

    def test_each_piece_updates_after_minimum_length_and_count_changes(self):
        app = self.app(2)
        app.number_input(key='tv_0_min_咖啡_2').set_value(4).run()
        self.assertEqual(self.summary(app)['最低總詞次'].tolist(), [2, 5])
        pick(app, 'selectbox', '每篇字數').set_value('100–150').run()
        self.assertEqual(self.summary(app)['篇幅上限'].tolist(), [150, 150])
        app.multiselect(key='tv_0_selected').set_value(['咖啡']).run()
        self.assertEqual(self.summary(app)['目標詞數量'].tolist(), [1, 1])
        self.assertEqual(self.summary(app)['最低總詞次'].tolist(), [1, 4])
        pick(app, 'selectbox', '生成篇數').set_value(1).run()
        self.assertEqual(self.summary(app)['篇章'].tolist(), [1])

    def test_empty_targets_keep_draft_and_generation_accessible(self):
        app = self.app()
        app.text_area(key='draft').set_value('教師草稿').run()
        app.multiselect(key='tv_0_selected').set_value([]).run()
        self.assertEqual(self.summary(app)['目標詞數量'].tolist(), [0])
        self.assertEqual(self.summary(app)['最低總詞次'].tolist(), [0])
        self.assertEqual(app.session_state['draft'], '教師草稿')
        self.assertIn('未選定目標詞', self.generation(app))

    def test_lesson_switch_uses_current_settings_and_restores_individual_values(self):
        app = self.app()
        app.number_input(key='tv_0_min_咖啡_1').set_value(8).run()
        self.assertEqual(self.summary(app)['最低總詞次'].tolist(), [9])
        app.text_area(key='draft').set_value('保留跨課草稿').run()
        pick(app, 'selectbox', '課別').set_value(1).run()
        current = app.session_state['target_config']
        self.assertEqual(self.summary(app)['目標詞數量'].tolist(), [len(current['targets'])])
        self.assertEqual(self.summary(app)['最低總詞次'].tolist(),
                         [sum(current['minimums'][w][1] for w in current['targets'])])
        self.assertEqual(app.session_state['draft'], '保留跨課草稿')
        pick(app, 'selectbox', '課別').set_value(0).run()
        self.assertEqual(self.summary(app)['最低總詞次'].tolist(), [9])

    def test_mocked_risk_never_blocks_prompt_or_changes_settings(self):
        # W02 policy simulation only, not a real feasibility criterion.
        app = self.app(2)
        module = importlib.import_module('generation_constraints')
        real = module.summarize_constraints
        before = copy.deepcopy(app.session_state['target_config'])
        calls = []
        def simulate(*args, **kwargs):
            calls.append(copy.deepcopy(args))
            result = real(*args, **kwargs)
            result['risks'] = [{'類別': 'B', '篇章': 2,
                                '說明': '測試替身：第2篇可能有條件衝突，待教師評估。'}]
            return result
        with patch.object(module, 'summarize_constraints', side_effect=simulate):
            app.run()
            self.healthy(app)
            self.assertTrue(any('測試替身' in e.value for e in app.info))
            self.assertIn('咖啡：第1篇至少1次、第2篇至少1次', self.generation(app))
            self.assertEqual(app.session_state['target_config'], before)
        self.assertTrue(calls)
        self.assertEqual(calls[-1][0], before)
        expected_names = list(dict.fromkeys(app.multiselect(key='names_0_對話1').value +
                             [name for line in app.text_area(key='extra_names_0_對話1').value.splitlines()
                              for name in re.split('[,，、]', line)]))
        self.assertEqual(calls[-1][2]['names'], expected_names)

    def test_format_warning_keeps_original_input_and_prompt(self):
        app = self.app()
        app.text_area(key='tv_0_manual').set_value('咖啡！').run()
        self.healthy(app)
        self.assertTrue(any('A｜' in w.value and '咖啡！' in w.value for w in app.warning))
        self.assertIn('咖啡！', self.generation(app))
        self.assertEqual(app.text_area(key='tv_0_manual').value, '咖啡！')
        app.text_area(key='tv_0_manual').set_value('').run()
        self.assertFalse(any('A｜' in w.value and '咖啡！' in w.value for w in app.warning))

    def test_generation_warning_does_not_replace_audit_or_targeted_revision(self):
        app = self.app()
        app.multiselect(key='tv_0_selected').set_value(['咖啡']).run()
        app.number_input(key='tv_0_min_咖啡_1').set_value(3).run()
        app.text_area(key='draft').set_value('咖啡。')
        pick(app, 'button', '執行 Python 檢測').click().run()
        self.healthy(app)
        row = app.session_state['last_audit']['逐篇複現']['rows'][0]
        self.assertEqual((row['最低次數'], row['確定出現次數'], row['確定不足量']), (3, 1, 2))
        self.assertTrue(any('至少還缺2次' in c.value for c in app.code))
        before = copy.deepcopy(app.session_state['target_config'])
        app.run()
        self.assertEqual(app.session_state['target_config'], before)
        self.assertEqual(app.session_state['draft'], '咖啡。')

    def test_requirement_change_revokes_old_audit_and_preserves_teacher_edits(self):
        app = self.app()
        app.text_area(key='draft').set_value('咖啡，茶。')
        pick(app, 'button', '執行 Python 檢測').click().run()
        pick(app, 'button', '將目前檢測內容確認為定稿').click().run()
        app.text_area(key='revision_notes').set_value('保留結局').run()
        app.number_input(key='tv_0_min_咖啡_1').set_value(4).run()
        self.assertEqual(self.summary(app)['最低總詞次'].tolist(), [5])
        self.assertNotIn('last_audit', app.session_state)
        self.assertFalse(app.session_state['approved'])
        self.assertEqual(app.session_state['draft'], '咖啡，茶。')
        self.assertEqual(app.text_area(key='revision_notes').value, '保留結局')

    def test_cross_book_and_manual_targets_use_current_inputs_without_old_notices(self):
        app = self.app()
        app.text_area(key='tv_0_manual').set_value('指定詞！').run()
        self.assertEqual(self.summary(app)['目標詞數量'].tolist(), [3])
        self.assertTrue(any('指定詞！' in w.value and 'A｜' in w.value for w in app.warning))
        pick(app, 'selectbox', '冊別').set_value('第二冊').run()
        self.healthy(app)
        current = app.session_state['target_config']
        self.assertGreaterEqual(current['lesson_id'], 15)
        self.assertEqual(self.summary(app)['目標詞數量'].tolist(), [len(current['targets'])])
        self.assertFalse(any('指定詞！' in w.value and 'A｜' in w.value for w in app.warning))
        self.assertNotIn('指定詞！', current['targets'])

    def test_changing_context_reaches_summary_without_adding_targets(self):
        app = self.app()
        module = importlib.import_module('generation_constraints')
        with patch.object(module, 'summarize_constraints', wraps=module.summarize_constraints) as spy:
            pick(app, 'text_area', '補充引導詞（選填，建議 2～3 個）').set_value('朋友')
            pick(app, 'text_area', '教師允許保留的額外詞（可選，每行一詞）').set_value('銀行')
            app.text_area(key='extra_names_0_對話1').set_value('新人物')
            pick(app, 'text_input', '新情境／主題（選填）').set_value('校園')
            app.run()
            self.healthy(app)
            args = spy.call_args.args
            self.assertEqual(args[0], app.session_state['target_config'])
            self.assertEqual(args[2]['guides'], ['朋友'])
            self.assertIn('銀行', args[2]['allowed'])
            self.assertIn('新人物', args[2]['names'])
            self.assertEqual(args[2]['theme'], '校園')
            self.assertTrue(args[2]['grammar'])
            self.assertEqual(args[2]['reference'], '對話1')
        self.assertEqual(app.session_state['target_config']['targets'], ['咖啡', '茶'])
        self.assertEqual(self.summary(app)['最低總詞次'].tolist(), [2])

    def test_uncertain_semantics_are_labeled_without_claiming_automatic_success(self):
        app = self.app()
        self.assertTrue(any('C｜' in e.value and '無法自動判定' in e.value for e in app.caption))
        self.assertTrue(any('B｜' in e.value and '待教師評估' in e.value for e in app.info))
        self.assertFalse(any('確定衝突' in w.value for w in app.warning))


if __name__ == '__main__':
    unittest.main()
