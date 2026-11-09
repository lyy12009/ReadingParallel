"""Offline regression tests; load pure functions without starting Streamlit."""
import ast
import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).absolute().parents[1]
TREE = ast.parse((ROOT / 'app.py').read_text(encoding='utf-8'))
FUNCTIONS = {'parse_learning_json', 'json_to_editors', 'questions_to_text',
             'inspect_structured_quiz'}
NS = {'json': json, 're': re}
exec(compile(ast.Module(body=[node for node in TREE.body
     if isinstance(node, ast.FunctionDef) and node.name in FUNCTIONS],
     type_ignores=[]), str(ROOT / 'app.py'), 'exec'), NS)


def sample():
    return {'title': '閱讀測驗', 'reading_texts': [{'title': '文本一', 'content': '今天上課。'}],
            'supplementary_vocabulary': [], 'instructions': '請選答案。',
            'questions': [{'type': 'single_choice', 'stem': '今天做什麼？',
                           'options': ['上課', '吃飯', '喝茶', '回家'], 'answer': 'A'}]}


class RegressionTests(unittest.TestCase):
    def test_stage_three_prompt_renders(self):
        # Execute the actual f-string with all legitimate prompt inputs.
        assignment = next(node for node in ast.walk(TREE)
                          if isinstance(node, ast.Assign) and any(
                              isinstance(t, ast.Name) and t.id == 'prompt3' for t in node.targets))
        inputs = dict(final='今天上課。', known_for_llm=['今天', '上課'],
                      llm_allowed=[], retained=[], glossary_language_text='English（英語）',
                      difference_section='', quiz_type='單選題', quiz_qty=3,
                      quiz_focus='訊息擷取與理解', quiz_basis='依閱讀理解目標',
                      change_instruction='依新文本命題。', glossary_languages=['English（英語）'], n=1)
        result = eval(compile(ast.Expression(assignment.value), '<prompt3>', 'eval'), inputs)
        self.assertIn('今天上課。', result)
        self.assertIn('single_choice', result)

    def test_invalid_reading_title_is_validation_error(self):
        for value in [123, None, [], {}]:
            with self.subTest(value=value):
                data = sample()
                data['reading_texts'][0]['title'] = value
                with self.assertRaises(ValueError):
                    parsed = NS['parse_learning_json'](json.dumps(data))
                    NS['json_to_editors'](parsed)

    def test_invalid_question_type_is_validation_error(self):
        for value in [[], {}, None, 123]:
            with self.subTest(value=value):
                data = sample()
                data['questions'][0]['type'] = value
                with self.assertRaises(ValueError):
                    parsed = NS['parse_learning_json'](json.dumps(data))
                    NS['inspect_structured_quiz'](parsed)

    def test_valid_json_and_editor_conversion(self):
        data = sample()
        parsed = NS['parse_learning_json']('```json\n' + json.dumps(data) + '\n```')
        self.assertEqual(NS['inspect_structured_quiz'](parsed, '單選題', 1), [])
        editors = NS['json_to_editors'](parsed)
        self.assertEqual(editors['v2_body'], '文本一\n今天上課。')
        self.assertEqual(editors['v2_answers'], '1. A')
        self.assertIn('A. 上課', editors['v2_questions'])

    def test_optional_fields_keep_existing_defaults(self):
        data = sample()
        del data['reading_texts'][0]['title']
        del data['questions'][0]['type']
        parsed = NS['parse_learning_json'](json.dumps(data))
        self.assertEqual(NS['json_to_editors'](parsed)['v2_body'], '今天上課。')
        self.assertEqual(NS['inspect_structured_quiz'](parsed, '單選題', 1), [])

    def test_all_supported_question_types(self):
        for kind, answer in [('single_choice', 'A'), ('true_false', '對'),
                             ('short_answer', '上課'), ('ordering', '2,1,3')]:
            with self.subTest(kind=kind):
                data = sample()
                data['questions'][0].update(type=kind, answer=answer)
                parsed = NS['parse_learning_json'](json.dumps(data))
                self.assertEqual(NS['inspect_structured_quiz'](parsed), [])

    def test_unknown_string_type_remains_structure_warning(self):
        data = sample()
        data['questions'][0]['type'] = 'unsupported'
        parsed = NS['parse_learning_json'](json.dumps(data))
        self.assertTrue(NS['inspect_structured_quiz'](parsed))


if __name__ == '__main__':
    unittest.main()
