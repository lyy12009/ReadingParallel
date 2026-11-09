"""Curriculum-only lexical decisions; never tokenizes or changes target counts."""
from copy import deepcopy
import re

VOCABULARY_RULE_VERSION = 'curriculum-two-character-exemption-v1'
_TWO_HAN = re.compile(r'[\u3400-\u9fff]{2}')


def classify_learned_word(word, known, text_evidence, character_sources=None):
    """Exact whole-word evidence first; only two Han characters may fall back."""
    if word in known:
        return {'判定來源': '教材生詞', '單字證據': []}
    if word in text_evidence:
        return {'判定來源': '已學課文斷詞', '單字證據': []}
    if not _TWO_HAN.fullmatch(word):
        return None
    sources = character_sources or {}
    evidence = []
    for character in word:
        categories = [name for name, values in [('教材生詞', known), ('已學課文斷詞', text_evidence)]
                      if character in values]
        if not categories:
            return None
        records = {category: deepcopy(sources.get(character, {}).get(category))
                   for category in categories}
        reliable = [record for record in records.values()
                    if isinstance(record, dict) and isinstance(record.get('資料索引'), int)]
        first = min(reliable, key=lambda r: (r['資料索引'], r.get('部分序', 0))) if reliable else None
        evidence.append({'單字詞': character, '證據類別': categories,
                         '來源紀錄': records, '首次來源': first})
    return {'判定來源': '已學單字組合豁免', '單字證據': evidence}


def exemption_table(records):
    """Readable view; raw structured evidence remains in the audit report."""
    rows = []
    for record in records:
        row = {'原詞': record['原詞'], '拆解單字詞': '＋'.join(e['單字詞'] for e in record['單字證據']),
               '原詞出現次數': record['原詞出現次數'], '判定結果': record['判定結果']}
        for number, evidence in enumerate(record['單字證據'], 1):
            row[f'單字{number}證據'] = '／'.join(evidence['證據類別'])
            first = evidence['首次來源']
            row[f'單字{number}首次來源'] = (
                f"{first['冊']} {first['課']}｜{first['來源']}" +
                (f"｜第{first['行號']}行" if '行號' in first else '')
                if first else '未提供可靠冊課來源')
        if '區域' in record:
            row['區域'] = record['區域']
        if '區塊' in record:
            row['區塊'] = record['區塊']
        rows.append(row)
    return rows

def quiz_review_table(rows):
    """Keep the established scalar quiz table; rich evidence has its own view."""
    return [{key: value for key, value in row.items() if key != '已學單字組合豁免明細'}
            for row in rows]
