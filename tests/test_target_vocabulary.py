"""Target configuration regressions: real AppTest, no LLM or browser mocks."""
import ast
import importlib
import json
from pathlib import Path
import unittest

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).absolute().parents[1]


def selectbox(app, label):
    return next(w for w in app.selectbox if w.label == label)


def button(app, label):
    return next(w for w in app.button if w.label == label)


class TargetModelTests(unittest.TestCase):
    def model(self):
        return importlib.import_module('target_settings')

    def test_merge_deduplicates_without_mutating_inputs(self):
        chosen = ['咖啡', '茶']
        self.assertEqual(self.model().combine_targets(chosen, '咖啡，機場\n機場；茶'),
                         ['咖啡', '茶', '機場'])
        self.assertEqual(chosen, ['咖啡', '茶'])

    def test_matrix_defaults_and_preserves_individual_values(self):
        matrix = self.model().minimum_matrix(['咖啡', '茶'], 3)
        self.assertEqual(matrix, {w: {p: 1 for p in (1, 2, 3)} for w in ['咖啡', '茶']})
        matrix['咖啡'][2] = 4
        self.assertEqual(self.model().minimum_matrix(['茶', '咖啡', '機場'], 3, matrix),
                         {'茶': {1: 1, 2: 1, 3: 1}, '咖啡': {1: 1, 2: 4, 3: 1},
                          '機場': {1: 1, 2: 1, 3: 1}})

    def test_all_part_candidates_deduplicate_even_without_source_text(self):
        # Execute the same words() helper used by the UI on an independent lesson.
        tree = ast.parse((ROOT / 'app.py').read_text(encoding='utf-8'))
        namespace = {'PARTS': ['對話1', '對話2', '文章1', '文章2']}
        nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                 and node.name in ('uniq', 'words')]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), '<candidate helper>', 'exec'),
             namespace)
        lesson = {'對話1生詞': ['咖啡', '茶'], '對話2生詞': ['茶', '咖啡'],
                  '文章1生詞': [], '文章2生詞': ['機場', '茶', '']}
        self.assertEqual(namespace['words'](lesson), ['咖啡', '茶', '機場'])
        self.assertEqual(self.model().combine_targets(namespace['words'](lesson), '機場'),
                         ['咖啡', '茶', '機場'])

    def test_invalidation_preserves_all_teacher_text(self):
        state = dict(draft='閱讀草稿', final_text='定稿', v2_body='編輯正文',
                     v2_questions='題目', approved=True, last_audit={'old': 1},
                     audited_text='閱讀草稿', audited_target_settings=('old',))
        self.model().invalidate_audit(state)
        self.assertFalse(state['approved'])
        self.assertNotIn('last_audit', state)
        self.assertNotIn('audited_target_settings', state)
        self.assertEqual([state[k] for k in ['draft', 'final_text', 'v2_body', 'v2_questions']],
                         ['閱讀草稿', '定稿', '編輯正文', '題目'])


class TargetUITests(unittest.TestCase):
    def app(self):
        app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=60).run()
        self.healthy(app)
        return app

    def healthy(self, app):
        self.assertEqual([e.message for e in app.exception], [])

    def configure(self, app, selected=('咖啡', '茶'), manual=''):
        app.multiselect(key='tv_0_selected').set_value(list(selected))
        app.text_area(key='tv_0_manual').set_value(manual)
        app.run()
        self.healthy(app)

    def audit_and_approve(self, app):
        app.text_area(key='draft').set_value('明華：你好。\n月美：今天上課。')
        button(app, '執行 Python 檢測').click().run()
        self.healthy(app)
        button(app, '將目前檢測內容確認為定稿').click().run()
        self.healthy(app)
        self.assertTrue(app.session_state['approved'])

    def assert_invalid(self, app, draft):
        self.healthy(app)
        self.assertFalse(app.session_state['approved'])
        self.assertNotIn('last_audit', app.session_state)
        self.assertEqual(app.session_state['draft'], draft)
        self.assertFalse(any('【定稿閱讀文本：必須原樣保留】' in c.value for c in app.code))

    def test_whole_lesson_candidates_merge_and_independent_roles(self):
        app = self.app()
        data = json.loads((ROOT / 'corpus.json').read_text(encoding='utf-8'))
        candidates = list(dict.fromkeys(w for part in ['對話1', '對話2', '文章1', '文章2']
                                       for w in data[0][part + '生詞']))
        self.assertEqual(app.multiselect(key='tv_0_selected').options, candidates)
        self.assertEqual(selectbox(app, '複現目標（來源文本）').value, '對話1')
        self.assertIn('咖啡', candidates)  # From dialogue 2, not the selected source.
        self.configure(app, manual='咖啡，機場\n機場')
        next(w for w in app.text_area if w.label.startswith('補充引導詞')).set_value('朋友')
        next(w for w in app.text_area if w.label.startswith('教師允許保留的額外詞')).set_value('銀行')
        app.run()
        self.healthy(app)
        self.assertEqual(app.session_state['target_config']['targets'], ['咖啡', '茶', '機場'])
        self.assertEqual(app.multiselect(key='tv_0_selected').options, candidates)
        app.text_area(key='tv_0_manual').set_value('咖啡、機場、朋友').run()
        self.healthy(app)
        self.assertEqual(app.session_state['target_config']['targets'], ['咖啡', '茶', '機場', '朋友'])

    def test_three_text_default_batch_and_cell_adjustment(self):
        app = self.app()
        self.configure(app)
        selectbox(app, '生成篇數').set_value(3).run()
        self.healthy(app)
        self.assertEqual(app.session_state['target_config']['minimums'],
                         {w: {p: 1 for p in (1, 2, 3)} for w in ['咖啡', '茶']})
        app.number_input(key='tv_0_uniform').set_value(2)
        app.button(key='tv_0_apply').click().run()
        app.number_input(key='tv_0_min_咖啡_2').set_value(3)
        app.number_input(key='tv_0_min_茶_3').set_value(4)
        app.run()
        self.healthy(app)
        self.assertEqual(app.session_state['target_config']['minimums'],
                         {'咖啡': {1: 2, 2: 3, 3: 2}, '茶': {1: 2, 2: 2, 3: 4}})
        app.text_area(key='draft').set_value('教師保留的草稿').run()
        selectbox(app, '生成篇數').set_value(2).run()
        selectbox(app, '生成篇數').set_value(3).run()
        self.healthy(app)
        self.assertEqual(app.session_state['target_config']['minimums']['茶'][3], 4)
        self.assertEqual(app.session_state['draft'], '教師保留的草稿')

    def test_target_changes_invalidate_approval_and_preserve_text(self):
        app = self.app()
        self.configure(app)
        self.audit_and_approve(app)
        draft = app.session_state['draft']
        app.multiselect(key='tv_0_selected').set_value(['咖啡']).run()
        self.assert_invalid(app, draft)
        app.multiselect(key='tv_0_selected').set_value(['咖啡', '茶']).run()
        self.assert_invalid(app, draft)  # Returning to old inputs cannot resurrect approval.
        self.audit_and_approve(app)
        app.text_area(key='tv_0_manual').set_value('機場').run()
        self.assert_invalid(app, draft)
        self.assertEqual(app.session_state['final_text'], draft)

    def test_count_changes_invalidate_approval_and_preserve_text(self):
        app = self.app()
        self.configure(app)
        self.audit_and_approve(app)
        draft = app.session_state['draft']
        app.number_input(key='tv_0_min_咖啡_1').set_value(2).run()
        self.assert_invalid(app, draft)
        self.audit_and_approve(app)
        app.number_input(key='tv_0_uniform').set_value(3)
        app.button(key='tv_0_apply').click().run()
        self.assert_invalid(app, draft)
        self.assertEqual(app.session_state['target_config']['minimums'],
                         {'咖啡': {1: 3}, '茶': {1: 3}})

    def test_reference_change_keeps_candidates_targets_counts_and_text(self):
        app = self.app()
        self.configure(app, manual='機場')
        app.number_input(key='tv_0_min_咖啡_1').set_value(2).run()
        self.audit_and_approve(app)
        candidates = app.multiselect(key='tv_0_selected').options
        config = app.session_state['target_config']
        draft = app.session_state['draft']
        selectbox(app, '複現目標（來源文本）').set_value('對話2').run()
        self.assert_invalid(app, draft)
        self.assertEqual(app.multiselect(key='tv_0_selected').options, candidates)
        self.assertEqual(app.session_state['target_config']['targets'], config['targets'])
        self.assertEqual(app.session_state['target_config']['minimums'], config['minimums'])

    def test_lesson_book_switch_does_not_leak_and_restores_settings(self):
        app = self.app()
        self.configure(app, manual='機場')
        app.number_input(key='tv_0_min_咖啡_1').set_value(4).run()
        app.text_area(key='draft').set_value('保留跨課草稿').run()
        selectbox(app, '課別').set_value(1).run()
        self.healthy(app)
        self.assertNotIn('機場', app.session_state['target_config']['targets'])
        self.assertEqual(app.session_state['draft'], '保留跨課草稿')
        selectbox(app, '冊別').set_value('第二冊').run()
        self.healthy(app)
        config = app.session_state['target_config']
        self.assertGreaterEqual(config['lesson_id'], 15)
        self.assertNotIn('機場', config['manual_targets'])
        selectbox(app, '冊別').set_value('第一冊').run()
        selectbox(app, '課別').set_value(0).run()
        self.healthy(app)
        self.assertEqual(app.session_state['target_config']['targets'], ['咖啡', '茶', '機場'])
        self.assertEqual(app.session_state['target_config']['minimums']['咖啡'][1], 4)
        self.assertEqual(app.session_state['draft'], '保留跨課草稿')

    def test_empty_selection_is_not_silently_repopulated(self):
        app = self.app()
        self.configure(app, selected=())
        self.assertEqual(app.session_state['target_config']['targets'], [])
        self.assertEqual(app.session_state['target_config']['minimums'], {})
        app.run()
        self.assertEqual(app.multiselect(key='tv_0_selected').value, [])

    def test_manual_target_is_not_automatically_allowed(self):
        app = self.app()
        self.configure(app, selected=(), manual='獨角獸太空港')
        app.text_area(key='draft').set_value('獨角獸太空港。')
        button(app, '執行 Python 檢測').click().run()
        self.healthy(app)
        report = app.session_state['last_audit']
        self.assertGreater(report['疑似超綱詞次'], 0)
        self.assertEqual(report['已學證據分類'].get('教師允許詞', 0), 0)

    def test_pending_batch_value_invalidates_without_silently_changing_cells(self):
        app = self.app()
        self.configure(app)
        self.audit_and_approve(app)
        draft = app.session_state['draft']
        app.number_input(key='tv_0_uniform').set_value(5).run()
        self.assert_invalid(app, draft)
        self.assertEqual(app.session_state['target_config']['minimums'],
                         {'咖啡': {1: 1}, '茶': {1: 1}})
        app.button(key='tv_0_apply').click().run()
        self.assertEqual(app.session_state['target_config']['minimums'],
                         {'咖啡': {1: 5}, '茶': {1: 5}})

    def test_text_count_and_form_changes_revoke_approval_but_keep_draft(self):
        app = self.app()
        self.configure(app)
        self.audit_and_approve(app)
        draft = app.session_state['draft']
        selectbox(app, '生成篇數').set_value(2).run()
        self.assert_invalid(app, draft)
        self.audit_and_approve(app)
        selectbox(app, '新文本形式').set_value('閱讀短文').run()
        self.assert_invalid(app, draft)

    def test_loaded_reading_is_preserved_and_explicit_reload_uses_current_draft(self):
        app = self.app()
        self.configure(app)
        self.audit_and_approve(app)
        button(app, '只載入已定稿閱讀文本（暫不命題）').click().run()
        app.text_area(key='v2_body').set_value('教師修改的匯出正文').run()
        app.multiselect(key='tv_0_selected').set_value(['茶']).run()
        self.healthy(app)
        self.assertFalse(app.session_state['approved'])
        self.assertEqual(app.session_state['v2_body'], '教師修改的匯出正文')
        app.text_area(key='draft').set_value('新草稿').run()
        button(app, '只載入已定稿閱讀文本（暫不命題）').click().run()
        self.healthy(app)
        self.assertEqual(app.session_state['v2_body'], '新草稿')

    def test_new_target_gets_one_without_destroying_saved_settings(self):
        app = self.app()
        self.configure(app)
        app.number_input(key='tv_0_min_咖啡_1').set_value(5).run()
        self.configure(app, selected=('茶',))
        self.configure(app, selected=('茶', '咖啡'), manual='機場')
        matrix = app.session_state['target_config']['minimums']
        self.assertEqual(matrix['咖啡'][1], 5)
        self.assertEqual(matrix['機場'][1], 1)


if __name__ == '__main__':
    unittest.main()
