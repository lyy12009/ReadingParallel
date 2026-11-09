"""Explicit offline acceptance replay; not part of unittest discovery.

Run: .venv/Scripts/python.exe -B tests/acceptance_replay.py
Uses real AppTest, curriculum, tokenizer and Word bytes. No LLM or GUI browser.
Fixed texts/answers are test material, not teacher-approved teaching content.
"""
import copy
import hashlib
import io
import json
from pathlib import Path
import re
import sys
from unittest.mock import patch

import streamlit as st
from docx import Document
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).absolute().parents[1]
OUTPUT = ROOT / 'test-results' / 'integration-acceptance'
LANGUAGES = ['English（英語）', '繁體中文（臺灣）']

CASES = [{'id': 'A',
  'book': '第三冊',
  'lesson_index': 0,
  'data_index': 30,
  'reference': '文章1',
  'selected': ['新生', '壓力'],
  'manual': '',
  'names': [],
  'pieces': [{'title': '安排讀書時間',
              'content': '新生今天第一次到學校上課。她覺得課很多，心裡有一點壓力。下課以後，她找老師討論讀書的方法。老師請她先看課表，再把每天要做的事寫下來。她照著老師的方法安排時間，晚上也早一點休息。過了一個星期，她上課比較有精神，也能按時做完功課。她知道自己還有很多要學，可是已經不再那麼擔心。'}],
  'questions': [{'stem': '新生找誰討論讀書的方法？', 'options': ['老師', '醫生', '司機', '警察'], 'answer': 'A'},
                {'stem': '老師請她先看什麼？', 'options': ['地圖', '課表', '報紙', '電影'], 'answer': 'B'},
                {'stem': '過了一個星期，她上課怎麼樣？',
                 'options': ['每天遲到', '都在睡覺', '比較有精神', '不再上課'],
                 'answer': 'C'}],
  'evidence': ['找老師討論讀書的方法', '老師請她先看課表', '她上課比較有精神']},
 {'id': 'B',
  'book': '第一冊',
  'lesson_index': 1,
  'data_index': 1,
  'reference': '對話1',
  'selected': ['照片', '妹妹'],
  'manual': '',
  'names': ['怡君'],
  'pieces': [{'title': '桌上的照片',
              'content': '怡君今天在家整理照片。她找到一張以前和妹妹一起照的相片，就請妹妹過來看。兩個人想起那天一起出門的事，都笑了起來。媽媽走進房間，看見她們這麼高興，也坐下來聊天。怡君決定把這張照片放在桌上，讓家人每天都能看見。吃飯以前，她們把房間整理好，再一起到客廳去。'},
             {'title': '給媽媽的禮物',
              'content': '妹妹想送媽媽一個小禮物。她請怡君幫忙，找出全家人一起玩的照片。怡君覺得這個主意很好，就拿出紙和筆，寫了一句謝謝媽媽的話。妹妹把紙放進禮物裡，又在外面畫了一個小小的愛心。晚上媽媽回到家，看見孩子準備的東西，開心得笑了。大家坐在一起，說起以前出遊的事情。'}],
  'cell': {'word': '照片', 'piece': 2, 'minimum': 2},
  'revision_old': '找出全家人一起玩的照片。',
  'revision_new': '找出全家人一起玩的照片，再選一張照片放進禮物裡。',
  'glossary': [{'word': '禮物',
                'source': ['reading'],
                'explanation': {'English（英語）': 'gift', '繁體中文（臺灣）': '送給別人的東西'}}],
  'questions': [{'stem': '怡君決定把照片放在哪裡？', 'options': ['桌上', '床下', '冰箱裡', '門外'], 'answer': 'A'},
                {'stem': '妹妹想送誰一個小禮物？', 'options': ['老師', '媽媽', '司機', '同學'], 'answer': 'B'},
                {'stem': '妹妹在禮物外面畫了什麼？',
                 'options': ['一朵花', '一隻鳥', '一個小小的愛心', '一輛車'],
                 'answer': 'C'}],
  'evidence': ['把這張照片放在桌上', '妹妹想送媽媽一個小禮物', '又在外面畫了一個小小的愛心']},
 {'id': 'C',
  'book': '第二冊',
  'lesson_index': 0,
  'data_index': 15,
  'reference': '文章1',
  'selected': [],
  'manual': '咖啡',
  'names': ['安同'],
  'pieces': [{'title': '照著地圖買東西',
              'content': '安同想買咖啡豆，出門以前先看手機裡的地圖。他從學校門口往前走，到了路口再右轉。走了一會兒，他看見一家小店，就進去問老闆有沒有他要買的東西。老闆拿了一包給他，還告訴他回家的路怎麼走。安同付了錢，把東西放進背包，照著老闆說的方向回到學校。他覺得今天沒有迷路，心裡很高興。'}],
  'glossary': [{'word': '豆',
                'source': ['reading'],
                'explanation': {'English（英語）': 'bean (here, part of coffee beans)',
                                '繁體中文（臺灣）': '豆子（此處指咖啡豆）'}}],
  'questions': [{'stem': '安同出門以前先看什麼？', 'options': ['手機裡的地圖', '電影', '報紙', '課表'], 'answer': 'A'},
                {'stem': '安同到了路口再怎麼走？', 'options': ['左轉', '右轉', '坐船', '上樓'], 'answer': 'B'},
                {'stem': '最後安同回到哪裡？', 'options': ['醫院', '郵局', '學校', '機場'], 'answer': 'C'}],
  'evidence': ['出門以前先看手機裡的地圖', '到了路口再右轉', '回到學校']}]


def pick(app, group, label):
    return next(widget for widget in getattr(app, group) if widget.label == label)


def healthy(app):
    assert not app.exception, [error.message for error in app.exception]


def text_of(pieces):
    return '\n\n'.join(f'【文本{i}】{piece["title"]}\n{piece["content"]}'
                       for i, piece in enumerate(pieces, 1))


def summary(app):
    columns = {'篇章', '目標詞數量', '最低總詞次', '篇幅下限', '篇幅上限'}
    return next(frame.value.to_dict('records') for frame in app.dataframe
                if columns <= set(frame.value.columns))


def revision_codes(app):
    return [code.value for code in app.code if '【修訂模式】' in code.value]


def audit(app, body):
    app.text_area(key='draft').set_value(body)
    pick(app, 'button', '執行 Python 檢測').click().run()
    healthy(app)
    assert app.session_state['audited_text'] == body
    assert not app.session_state['approved']
    return copy.deepcopy(app.session_state['last_audit'])


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    data = json.loads((ROOT / 'corpus.json').read_text(encoding='utf-8'))
    protected = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                 for name in ['corpus.json', 'dict.txt.big']}
    evidence = []
    original_download = st.download_button
    for original_case in CASES:
        case = copy.deepcopy(original_case)
        directory = OUTPUT / case['id']
        directory.mkdir(exist_ok=True)
        downloads = {}

        def capture(*args, **kwargs):
            downloads[kwargs['file_name']] = kwargs['data']
            return original_download(*args, **kwargs)

        with patch.object(st, 'download_button', side_effect=capture):
            app = AppTest.from_file(str(ROOT / 'app.py'), default_timeout=90).run()
            healthy(app)
            pick(app, 'selectbox', '冊別').set_value(case['book']).run()
            pick(app, 'selectbox', '課別').set_value(case['lesson_index']).run()
            pick(app, 'selectbox', '複現目標（來源文本）').set_value(case['reference']).run()
            pick(app, 'selectbox', '新文本形式').set_value('閱讀短文')
            pick(app, 'selectbox', '生成篇數').set_value(len(case['pieces']))
            pick(app, 'selectbox', '每篇字數').set_value('100–150')
            pick(app, 'selectbox', '命題依據').set_value('依閱讀理解目標')
            pick(app, 'multiselect', '補充生詞解釋語言（可多選）').set_value(LANGUAGES)
            app.run()
            idx = case['data_index']
            prefix = f'tv_{idx}'
            lesson = data[idx]
            assert lesson['冊'] == case['book']
            assert lesson[case['reference']]
            candidates = set(word for part in ['對話1', '對話2', '文章1', '文章2']
                             for word in lesson.get(part + '生詞', []))
            assert set(case['selected']) <= candidates
            app.multiselect(key=prefix + '_selected').set_value(case['selected'])
            app.text_area(key=prefix + '_manual').set_value(case['manual'])
            app.multiselect(key=f'names_{idx}_{case["reference"]}').set_value(case['names'])
            app.text_area(key=f'extra_names_{idx}_{case["reference"]}').set_value('')
            app.run()
            if case.get('cell'):
                cell = case['cell']
                app.number_input(key=f'{prefix}_min_{cell["word"]}_{cell["piece"]}').set_value(cell['minimum']).run()
            healthy(app)
            configured = copy.deepcopy(app.session_state['target_config'])
            quantities = summary(app)
            assert any('【生成條件】' in code.value for code in app.code)
            assert any('B｜' in item.value for item in app.info)
            body = text_of(case['pieces'])
            (directory / 'initial_text.txt').write_text(body, encoding='utf-8')
            initial = audit(app, body)
            rows = initial['逐篇複現']['rows']
            assert not initial['逐篇複現']['issues']
            if case['id'] == 'A':
                assert all(row['狀態'] == '達標' for row in rows)
                assert '一般文字修訂' in revision_codes(app)[0]
            elif case['id'] == 'B':
                deficits = [r for r in rows if r['狀態'] == '未達標']
                assert [(r['目標詞'], r['篇章'], r['確定不足量']) for r in deficits] == [('照片', 2, 1)]
                app.text_area(key='revision_notes').set_value('保留送媽媽禮物的主要事件與結局。').run()
                prompt = revision_codes(app)[0]
                assert '第2篇｜照片｜最低2次｜確定1次｜至少還缺1次' in prompt
                assert '第1篇｜照片' not in prompt
                (directory / 'revision_prompt.txt').write_text(prompt, encoding='utf-8')
                case['pieces'][1]['content'] = case['pieces'][1]['content'].replace(
                    case['revision_old'], case['revision_new'])
                body = text_of(case['pieces'])
                app.text_area(key='draft').set_value(body).run()
                healthy(app)
                assert 'last_audit' not in app.session_state
                assert not revision_codes(app)
                assert app.text_area(key='revision_notes').value == '保留送媽媽禮物的主要事件與結局。'
                audit(app, body)
                assert all(r['狀態'] == '達標' for r in app.session_state['last_audit']['逐篇複現']['rows'])
            else:
                assert [(r['確定出現次數'], r['可疑出現次數'], r['狀態'])
                        for r in rows] == [(0, 1, '待核對')]
                assert not revision_codes(app)
                assert initial['逐篇複現']['ambiguities']
                assert '咖啡' in {word for record in data[:idx+1]
                                  for part in ['對話1', '對話2', '文章1', '文章2']
                                  for word in record.get(part + '生詞', [])}
            current = copy.deepcopy(app.session_state['last_audit'])
            if case.get('glossary'):
                requested = [v['word'] for v in case['glossary']]
                assert all(word in current['疑似超綱'] for word in requested)
                app.multiselect(key='retained_words').set_value(requested).run()
            assert app.session_state['target_config'] == configured
            approve = pick(app, 'button', '將目前檢測內容確認為定稿')
            assert not approve.disabled
            approve.click().run()  # Simulates an explicit teacher action, not educational approval.
            healthy(app)
            assert app.session_state['approved']
            assert app.session_state['final_text'] == body
            assert app.session_state['last_audit']['逐篇複現'] == current['逐篇複現']
            prompt3 = next(code.value for code in app.code if '【定稿閱讀文本：必須原樣保留】' in code.value)
            assert body in prompt3 and 'single_choice' in prompt3
            fixture = {
                'title': f'整合驗收案例{case["id"]}（固定離線測試）',
                'reading_texts': [{'title': f'【文本{i}】{piece["title"]}', 'content': piece['content']}
                                  for i, piece in enumerate(case['pieces'], 1)],
                'supplementary_vocabulary': case.get('glossary', []),
                'instructions': '請依閱讀文本選出正確答案。',
                'questions': [dict(question, type='single_choice') for question in case['questions']],
                'validation_warnings': []}
            for question, support in zip(fixture['questions'], case['evidence']):
                assert support in body
                answer = question['options'][ord(question['answer'])-ord('A')]
                assert len(question['options']) == len(set(question['options'])) == 4
                assert answer in support
                assert sum(option in support for option in question['options']) == 1
            (directory / 'quiz.json').write_text(json.dumps(fixture, ensure_ascii=False, indent=2), encoding='utf-8')
            (directory / 'final_text.txt').write_text(body, encoding='utf-8')
            app.text_area(key='v2_raw_json').set_value(json.dumps(fixture, ensure_ascii=False))
            pick(app, 'button', '解析 JSON 並填入以下編輯區').click().run()
            healthy(app)
            assert app.session_state['v2_body'] == body
            assert app.session_state['v22_json_structure_issues'] == []
            assert app.session_state['v22_json_vocab_issues'] == []
            assert app.session_state['v2_answers'] == '1. A\n2. B\n3. C'
            vocab_frames = [frame.value.to_dict('records') for frame in app.dataframe
                            if '尚未解決' in frame.value.columns]
            original_instructions = app.session_state['v2_instructions']
            before_quiz_review = copy.deepcopy(vocab_frames)
            app.text_area(key='v2_instructions').set_value(original_instructions + '獨角獸太空港。').run()
            healthy(app)
            after_quiz_review = [frame.value.to_dict('records') for frame in app.dataframe
                                 if '尚未解決' in frame.value.columns]
            assert before_quiz_review != after_quiz_review
            app.text_area(key='v2_instructions').set_value(original_instructions).run()
            healthy(app)
            word_checks = []
            assert len(downloads) == 2
            for name, content in downloads.items():
                doc = Document(io.BytesIO(content))
                doc_text = '\n'.join([p.text for p in doc.paragraphs] +
                                      [cell.text for table in doc.tables for row in table.rows for cell in row.cells])
                for piece in case['pieces']:
                    assert piece['content'] in doc_text
                for question in fixture['questions']:
                    assert question['stem'] in doc_text
                for vocab in fixture['supplementary_vocabulary']:
                    for meaning in vocab['explanation'].values():
                        assert meaning in doc_text
                if '教師版' in name:
                    assert '教師答案' in doc_text and '1. A\n2. B\n3. C' in doc_text
                else:
                    assert '教師答案' not in doc_text
                    assert all(answer not in doc_text for answer in ['1. A', '2. B', '3. C'])
                assert abs(doc.sections[0].page_width.cm - 21) < .1
                (directory / name).write_bytes(content)
                word_checks.append({'file': name, 'bytes': len(content), 'body_questions_answers': 'PASS'})
            if case.get('glossary'):
                app.checkbox(key='v2_show_vocab').set_value(False).run()
                healthy(app)
                for content in downloads.values():
                    hidden = Document(io.BytesIO(content))
                    hidden_text = '\n'.join([p.text for p in hidden.paragraphs] +
                                             [cell.text for table in hidden.tables for row in table.rows for cell in row.cells])
                    for entry in case['glossary']:
                        assert entry['explanation']['English（英語）'] not in hidden_text
                app.checkbox(key='v2_show_vocab').set_value(True).run()
                healthy(app)
            # Observe known Word-editor gap without fixing it or accepting edited content.
            old_final = app.session_state['final_text']
            app.text_area(key='v2_body').set_value(body + '\n獨角獸太空港。').run()
            healthy(app)
            editor_gap = (app.session_state['approved'] and
                          app.session_state['audited_text'] == old_final and
                          app.session_state['draft'] == old_final and
                          app.session_state['v2_body'] != old_final)
            app.text_area(key='v2_body').set_value(body).run()
            # Cross-batch invalidation retains both teacher text stores and history.
            app.number_input(key=f'{prefix}_min_{configured["targets"][0]}_1').set_value(
                configured['minimums'][configured['targets'][0]][1] + 1).run()
            healthy(app)
            assert 'last_audit' not in app.session_state and not app.session_state['approved']
            assert app.session_state['draft'] == body and app.session_state['v2_body'] == body
            assert app.session_state['final_text'] == old_final
            assert not revision_codes(app)
            evidence.append({
                'case': case['id'], 'book': lesson['冊'], 'lesson': lesson['課'], 'data_index': idx,
                'reference': case['reference'], 'target_config': configured, 'summary': quantities,
                'initial_rows': rows, 'export_rows': current['逐篇複現']['rows'],
                'ambiguities': current['逐篇複現']['ambiguities'],
                'reading_unknown': dict(current['疑似超綱']),
                'han_per_piece': [len(re.findall(r'[\u3400-\u9fff]',p['content'])) for p in case['pieces']],
                'quiz_unresolved_frames': vocab_frames, 'word': word_checks,
                'known_word_editor_gap_observed': editor_gap,
                'post_export_setting_invalidation': 'PASS',
                'quiz_edit_recheck_observed': True,
                'glossary_print_toggle': 'PASS' if case.get('glossary') else 'NOT APPLICABLE',
                'educational_acceptance': 'NOT PERFORMED; C ambiguity remains unresolved'})
        print('PASS: offline acceptance replay ' + case['id'])
    for name, digest in protected.items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
    (OUTPUT / 'evidence.json').write_text(json.dumps(
        {'protected_sha256': protected, 'cases': evidence}, ensure_ascii=False, indent=2), encoding='utf-8')
    print('PASS: three real-lesson offline AppTest replays, six Word files; data hashes unchanged')


if __name__ == '__main__':
    main()
