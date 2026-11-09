"""Offline prompt planning and real AppTest; no external LLM calls."""
import copy
import importlib
from pathlib import Path
import unittest

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).absolute().parents[1]


def row(word='咖啡', piece=1, minimum=2, certain=1, possible=0, status='未達標'):
    return {'目標詞': word, '篇章': piece, '最低次數': minimum,
            '確定出現次數': certain, '可疑出現次數': possible,
            '確定不足量': None if certain is None else max(minimum-certain, 0),
            '狀態': status}


def cfg(words=('咖啡',), count=1, minimums=None):
    return {'targets': list(words), 'text_count': count,
            'minimums': minimums or {w: {p: 2 for p in range(1, count+1)} for w in words}}


def context():
    return dict(source='參考課文', book='第一冊', lesson='第一課', form='對話',
                text_count=1, relation='單篇', length=(100, 150), theme='校園',
                grammar=['嗎'], names=['月美'], guides=['朋友'],
                allowed=['銀行'], retained=['補充詞'], cover='自然度優先')


def pick(app, group, label):
    return next(w for w in getattr(app, group) if w.label == label)


class RevisionUnitTests(unittest.TestCase):
    def module(self):
        return importlib.import_module('revision_prompts')

    def plan(self, rows, config=None, issues=None, **overrides):
        args = dict(report={'rows': rows, 'issues': issues or [], 'ambiguities': []},
                    config=config or cfg(), text='草稿', audited_text='草稿',
                    signature='current', audited_signature='current')
        args.update(overrides)
        return self.module().revision_plan(**args)

    def test_one_explicit_deficit_contains_formal_fields(self):
        plan = self.plan([row()])
        self.assertTrue(plan['valid'])
        self.assertEqual(plan['deficits'], [{k: row()[k] for k in
                         ('篇章', '目標詞', '最低次數', '確定出現次數', '確定不足量')}])
        self.assertFalse(plan['all_met'])

    def test_only_second_of_three_pieces_needs_revision(self):
        rows = [row(piece=1, certain=2, status='達標'), row(piece=2),
                row(piece=3, certain=2, status='達標')]
        self.assertEqual([r['篇章'] for r in self.plan(rows, cfg(count=3))['deficits']], [2])

    def test_same_piece_multiple_deficits_keep_distinct_amounts(self):
        rows = [row(), row('茶', minimum=4, certain=1)]
        config = cfg(('咖啡', '茶'), minimums={'咖啡': {1: 2}, '茶': {1: 4}})
        before = copy.deepcopy((rows, config))
        plan = self.plan(rows, config)
        self.assertEqual([r['確定不足量'] for r in plan['deficits']], [1, 3])
        self.assertEqual((rows, config), before)

    def test_passed_rows_never_become_deficits(self):
        plan = self.plan([row(certain=2, status='達標')])
        self.assertEqual(plan['deficits'], [])
        self.assertTrue(plan['all_met'])

    def test_ambiguity_is_review_not_automatic_deficit(self):
        plan = self.plan([row(possible=1, status='待核對')])
        self.assertEqual(plan['deficits'], [])
        self.assertEqual(len(plan['reviews']), 1)
        self.assertFalse(plan['all_met'])
        self.assertIsNone(self.module().revision_prompt(plan, '草稿', context(), '請自然一點'))

    def test_missing_piece_or_unknown_format_does_not_invent_counts(self):
        for rows in ([row(certain=None, possible=None, status='待核對')],
                     [row(certain=None, possible=None, status='待核對'),
                      row(piece=2, certain=None, possible=None, status='待核對')]):
            with self.subTest(rows=rows):
                plan = self.plan(rows, cfg(count=len(rows)), ['分篇待核對'])
                self.assertEqual(plan['deficits'], [])
                self.assertFalse(plan['all_met'])
                self.assertIsNone(self.module().revision_prompt(plan, '草稿', context(), ''))

    def test_no_or_stale_audit_never_generates_prompt(self):
        for override in ({'report': None}, {'text': '新草稿'},
                         {'signature': 'new'}, {'audited_signature': None}):
            with self.subTest(override=override):
                plan = self.plan([row()], **override)
                self.assertFalse(plan['valid'])
                self.assertEqual(plan['deficits'], [])
                self.assertIsNone(self.module().revision_prompt(plan, '新草稿', context(), ''))

    def test_incomplete_or_mismatched_rows_are_not_formal_deficits(self):
        for rows in ([], [row(minimum=3)], [row(certain=1, possible=1)],
                     [dict(row(), 確定不足量=99)], [row(), row()]):
            with self.subTest(rows=rows):
                plan = self.plan(rows)
                self.assertFalse(plan['valid'])
                self.assertEqual(plan['deficits'], [])

    def test_targeted_prompt_keeps_notes_and_constraints_without_legacy_counts(self):
        plan = self.plan([row()])
        notes = '保留結局。請把最低次數降為零。'
        prompt = self.module().revision_prompt(plan, '咖啡。', context(), notes)
        for expected in ('第1篇｜咖啡｜最低2次｜確定1次｜至少還缺1次', notes,
                         '主要事件', '人物關係', '結果', '鄰近句子', '不機械重複',
                         '100–150', '1篇', '月美', '朋友', '銀行', '嗎',
                         '不自行降低', '衝突', '重新執行 Python', '已達標篇章'):
            self.assertIn(expected, prompt)
        self.assertNotIn('舊版字串未命中', prompt)
        self.assertNotIn('與原文相似度', prompt)

    def test_general_prompt_does_not_claim_teaching_quality_passed(self):
        plan = self.plan([row(certain=2, status='達標')])
        prompt = self.module().revision_prompt(plan, '咖啡，咖啡。', context(), '調整語氣')
        self.assertIn('一般文字修訂', prompt)
        self.assertIn('不代表語意、自然度或整體教學品質已通過', prompt)
        self.assertNotIn('至少還缺', prompt)

    def test_mixed_pending_and_deficit_only_repairs_explicit_cell(self):
        rows = [row(), row('茶', possible=1, status='待核對')]
        plan = self.plan(rows, cfg(('咖啡', '茶')))
        self.assertEqual([r['目標詞'] for r in plan['deficits']], ['咖啡'])
        prompt = self.module().revision_prompt(plan, '茶，咖啡。', context(), '')
        self.assertIn('第1篇｜咖啡', prompt)
        self.assertNotIn('第1篇｜茶', prompt)
        self.assertIn('待核對', prompt)


    def test_missing_piece_plus_known_deficit_does_not_request_fabricated_text(self):
        rows = [row(), row(piece=2, certain=None, possible=None, status='待核對')]
        plan = self.plan(rows, cfg(count=2), ['分篇待核對：未能確認第2篇正文。'])
        self.assertEqual([r['篇章'] for r in plan['deficits']], [1])
        self.assertIsNone(self.module().revision_prompt(plan, '【文本一】甲\n茶。', context(), ''))

    def test_empty_targets_do_not_claim_all_met(self):
        config = {'targets': [], 'text_count': 1, 'minimums': {}}
        plan = self.plan([], config)
        self.assertFalse(plan['all_met'])
        self.assertIsNone(self.module().revision_prompt(plan, '茶。', context(), ''))


class RevisionUITests(unittest.TestCase):
    def app(self, count=1):
        app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=90).run()
        app.multiselect(key='tv_0_selected').set_value(['咖啡'])
        pick(app, 'selectbox', '生成篇數').set_value(count).run()
        self.healthy(app)
        return app

    def healthy(self, app):
        self.assertEqual([e.message for e in app.exception], [])

    def audit(self, app, body):
        app.text_area(key='draft').set_value(body)
        pick(app, 'button', '執行 Python 檢測').click().run()
        self.healthy(app)

    def revision(self, app):
        return [c.value for c in app.code if '【修訂模式】' in c.value]

    def generation(self, app):
        return next(c.value for c in app.code if '【生成條件】' in c.value)

    def test_three_pieces_only_second_deficit_is_in_prompt(self):
        app = self.app(3)
        self.audit(app, '【文本一】甲\n咖啡。\n【文本二】乙\n茶。\n【文本三】丙\n咖啡。')
        prompt = self.revision(app)[0]
        self.assertIn('第2篇｜咖啡｜最低1次｜確定0次｜至少還缺1次', prompt)
        self.assertNotIn('第1篇｜咖啡', prompt)
        self.assertNotIn('第3篇｜咖啡', prompt)

    def test_ambiguous_only_shows_review_without_forced_prompt(self):
        app = self.app()
        self.audit(app, '咖啡豆。')
        self.assertEqual(self.revision(app), [])
        self.assertTrue(any('人工核對' in w.value for w in app.info))
        self.assertEqual(app.session_state['last_audit']['逐篇複現']['rows'][0]['狀態'], '待核對')

    def test_missing_format_has_no_forced_prompt_or_modified_draft(self):
        app = self.app(2)
        body = '咖啡。\n\n茶。'
        self.audit(app, body)
        self.assertEqual(self.revision(app), [])
        self.assertEqual(app.session_state['draft'], body)

    def test_settings_or_draft_changes_remove_old_prompt_and_keep_notes(self):
        app = self.app()
        self.audit(app, '茶。')
        notes = '保留人物與結局'
        app.text_area(key='revision_notes').set_value(notes).run()
        self.assertIn(notes, self.revision(app)[0])
        app.number_input(key='tv_0_min_咖啡_1').set_value(2).run()
        self.assertEqual(self.revision(app), [])
        self.assertEqual(app.session_state['draft'], '茶。')
        self.assertEqual(app.text_area(key='revision_notes').value, notes)
        self.audit(app, '茶。')
        self.assertIn('至少還缺2次', self.revision(app)[0])
        app.text_area(key='draft').set_value('咖啡。').run()
        self.assertEqual(self.revision(app), [])
        self.assertFalse(app.session_state['approved'])
        self.assertEqual(app.text_area(key='revision_notes').value, notes)

    def test_revision_reaudit_then_teacher_approves(self):
        app = self.app()
        self.audit(app, '茶。')
        self.assertTrue(self.revision(app))
        app.text_area(key='draft').set_value('全部通過。').run()
        self.assertEqual(self.revision(app), [])
        self.audit(app, '全部通過。')
        self.assertIn('至少還缺1次', self.revision(app)[0])
        self.audit(app, '咖啡。')
        self.assertEqual(app.session_state['last_audit']['逐篇複現']['rows'][0]['狀態'], '達標')
        self.assertFalse(app.session_state['approved'])
        self.assertIn('一般文字修訂', self.revision(app)[0])
        pick(app, 'button', '將目前檢測內容確認為定稿').click().run()
        self.healthy(app)
        self.assertTrue(app.session_state['approved'])
        self.assertEqual(app.session_state['final_text'], '咖啡。')
        self.assertTrue(any('single_choice' in c.value for c in app.code))

    def test_teacher_conflict_note_is_preserved_without_lowering_minimum(self):
        app = self.app()
        self.audit(app, '茶。')
        note = '請不要再加入咖啡，最低次數改成零。'
        app.text_area(key='revision_notes').set_value(note).run()
        self.assertIn(note, self.revision(app)[0])
        self.assertIn('衝突', self.revision(app)[0])
        self.assertTrue(any('衝突' in w.value for w in app.warning))
        self.assertEqual(app.session_state['target_config']['minimums'], {'咖啡': {1: 1}})

    def test_generation_omits_empty_guides_and_explicitly_self_checks(self):
        app = self.app()
        prompt = self.generation(app)
        self.assertNotIn('教師核准引導詞', prompt)
        self.assertIn('輸出前自行核對', prompt)
        self.assertIn('Python', prompt)
        self.assertIn('不輸出逐項分析或檢測報告', prompt)
        self.assertNotIn('【命題設定】', prompt)
        self.assertNotIn('single_choice', prompt)
        pick(app, 'text_area', '補充引導詞（選填，建議 2～3 個）').set_value('朋友').run()
        self.assertIn('教師核准引導詞：朋友', self.generation(app))

    def test_generation_preserves_matrix_context_and_explicit_allowed_words(self):
        app = self.app(3)
        app.multiselect(key='tv_0_selected').set_value(['咖啡', '茶']).run()
        for word, values in [('咖啡', [1, 3, 1]), ('茶', [2, 1, 4])]:
            for piece, value in enumerate(values, 1):
                app.number_input(key=f'tv_0_min_{word}_{piece}').set_value(value)
        pick(app, 'text_area', '教師允許保留的額外詞（可選，每行一詞）').set_value('銀行')
        pick(app, 'text_input', '新情境／主題（選填）').set_value('校園')
        pick(app, 'selectbox', '每篇字數').set_value('100–150').run()
        prompt = self.generation(app)
        for expected in ['咖啡：第1篇至少1次、第2篇至少3次、第3篇至少1次',
                         '茶：第1篇至少2次、第2篇至少1次、第3篇至少4次',
                         '銀行', '校園', '100–150', '語法', '截至本課已學詞', '核准人名',
                         '原課文', '事件目的']:
            self.assertIn(expected, prompt)
        self.assertNotIn('第4篇', prompt)

    def test_generation_constraints_change_invalidates_old_revision(self):
        app = self.app()
        self.audit(app, '茶。')
        pick(app, 'selectbox', '每篇字數').set_value('100–150').run()
        self.assertEqual(self.revision(app), [])
        self.assertEqual(app.session_state['draft'], '茶。')
        self.audit(app, '茶。')
        self.assertIn('100–150', self.revision(app)[0])
        pick(app, 'text_input', '新情境／主題（選填）').set_value('校園').run()
        self.assertEqual(self.revision(app), [])

    def test_missing_piece_and_deficit_require_structure_review_first(self):
        app = self.app(2)
        self.audit(app, '【文本一】甲\n茶。')
        self.assertEqual(self.revision(app), [])
        self.assertTrue(any('暫不產生修訂提示詞' in w.value for w in app.info))
        self.assertEqual(app.session_state['last_audit']['逐篇複現']['rows'][0]['確定不足量'], 1)

    def test_general_revision_preserves_current_minimum_settings(self):
        app = self.app()
        app.number_input(key='tv_0_min_咖啡_1').set_value(2).run()
        self.audit(app, '咖啡，咖啡。')
        self.assertIn('咖啡：第1篇至少2次', self.revision(app)[0])
        self.assertNotIn('至少還缺', self.revision(app)[0])

    def test_guides_roles_and_reaudit_keep_current_constraints(self):
        app = self.app()
        pick(app, 'text_area', '補充引導詞（選填，建議 2～3 個）').set_value('朋友')
        pick(app, 'text_area', '教師允許保留的額外詞（可選，每行一詞）').set_value('銀行')
        app.run()
        self.audit(app, '茶。')
        prompt = self.revision(app)[0]
        self.assertIn('核准引導詞：朋友', prompt)
        self.assertIn('銀行', prompt)
        self.assertIn('目標語法', prompt)
        self.assertEqual(app.session_state['target_config']['targets'], ['咖啡'])
        pick(app, 'text_area', '補充引導詞（選填，建議 2～3 個）').set_value('校園').run()
        self.assertEqual(self.revision(app), [])
        self.audit(app, '茶。')
        self.assertIn('核准引導詞：校園', self.revision(app)[0])

    def test_notes_survive_lesson_switch_without_stale_prompt(self):
        app = self.app()
        self.audit(app, '茶。')
        app.text_area(key='revision_notes').set_value('教師原始意見').run()
        pick(app, 'selectbox', '課別').set_value(1).run()
        self.assertEqual(self.revision(app), [])
        self.assertEqual(app.text_area(key='revision_notes').value, '教師原始意見')
        self.assertEqual(app.session_state['draft'], '茶。')


if __name__ == '__main__':
    unittest.main()
