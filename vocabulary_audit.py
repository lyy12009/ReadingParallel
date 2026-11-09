"""Per-text target counts, using caller-supplied existing lexical views.

This module assigns explicit text labels and records token-boundary evidence.
It never adds targets to a dictionary or decides compound-word teaching rules.
"""
from collections import Counter
import re

_NUMBERS = {w: i for i, w in enumerate('一二三四五六七八九十', 1)}
_LABEL = r'[\d一二三四五六七八九十]+'
_HEADER = re.compile(
    r'^\s*(?:#{1,6}\s*)?(?:【\s*文本\s*(' + _LABEL +
    r')\s*】|文本\s*(' + _LABEL + r')(?:\s*[:：.、]\s*|\s*$))(.*)$')
_HEADER_LIKE = re.compile(r'^\s*(?:#{1,6}\s*)?(?:【\s*文本|文本\s*[\d一二三四五六七八九十甲乙丙])')


def split_texts(text, expected_count):
    """Return original source lines; never infer multiple pieces from blank lines."""
    records, prefix, issues = [], [], []
    current = None
    unsafe = False
    for line_number, raw in enumerate(text.splitlines(), 1):
        match = _HEADER.match(raw)
        if match:
            label = match.group(1) or match.group(2)
            number = int(label) if label.isdecimal() else _NUMBERS.get(label)
            if number is None:
                unsafe = True
                issues.append(f'分篇編號無法辨識：第{line_number}行。')
            current = {'number': number, 'title': match.group(3).strip(),
                       'header_line': line_number, 'lines': []}
            records.append(current)
        else:
            if _HEADER_LIKE.match(raw):
                unsafe = True
                issues.append(f'分篇標記格式不明：第{line_number}行。')
            if current is None:
                prefix.append((line_number, raw))
            else:
                current['lines'].append((line_number, raw))
    if not records:
        if expected_count == 1 and not unsafe and text.strip():
            records = [{'number': 1, 'title': '', 'header_line': None, 'lines': prefix}]
        else:
            issues.append('分篇不足或格式不明：多篇請各以【文本一】、【文本二】等獨立標題行標示，正文另起一行。')
            unsafe = True
    elif any(raw.strip() for _, raw in prefix):
        issues.append('分篇標題前有未歸屬正文，請確認篇章邊界。')
        unsafe = True

    numbers = [r['number'] for r in records]
    valid_numbers = [p for p in numbers if isinstance(p, int)]
    if len(set(numbers)) != len(numbers):
        issues.append('分篇編號重複，不能將重複區塊合併當成已達標。')
        unsafe = True
    if valid_numbers != sorted(valid_numbers):
        issues.append('分篇編號順序不明，請依篇號順序標示。')
        unsafe = True
    if any(p is None or p < 1 or p > expected_count for p in numbers):
        issues.append('分篇編號超出目前設定的篇章範圍。')
        unsafe = True
    if len(records) > expected_count:
        issues.append('分篇數多於目前設定，請核對。')
        unsafe = True
    assigned = {} if unsafe else {r['number']: r for r in records}
    for piece in range(1, expected_count + 1):
        if piece not in assigned:
            issues.append(f'分篇待核對：未能確認第{piece}篇正文。')
    return {'assigned': assigned, 'pieces': records, 'issues': issues}


def _token_spans(tokens, fragment):
    tokens = list(tokens)
    if any(not isinstance(token, str) or not token for token in tokens) or ''.join(tokens) != fragment:
        raise ValueError('斷詞結果無法與原文對齊')
    spans, position = set(), 0
    for token in tokens:
        spans.add((position, position + len(token), token))
        position += len(token)
    return tokens, spans


def _piece_counts(record, targets, token_views, clean_line, fragment_pattern):
    certain, suspect, traces, issues = Counter(), Counter(), [], []
    has_body = False
    for line_number, raw in record['lines']:
        cleaned = clean_line(raw)
        if not cleaned.strip():
            continue
        has_body = True
        body_start = raw.rfind(cleaned)
        if body_start < 0:
            issues.append(f'第{record["number"]}篇第{line_number}行清理結果無法對齊原文。')
            continue
        for fragment_match in fragment_pattern.finditer(cleaned):
            fragment = fragment_match.group()
            try:
                curriculum, jieba = token_views(fragment)
                curriculum, first = _token_spans(curriculum, fragment)
                jieba, second = _token_spans(jieba, fragment)
            except (ValueError, TypeError) as error:
                issues.append(f'第{record["number"]}篇第{line_number}行斷詞待核對：{error}')
                continue
            for word in targets:
                # Substring search enumerates candidates only; it is NOT the count criterion.
                start = fragment.find(word)
                while start >= 0:
                    end = start + len(word)
                    if (start, end, word) in first and (start, end, word) in second:
                        certain[word] += 1
                    else:
                        suspect[word] += 1
                        inside = any(a <= start and b >= end and b - a > len(word)
                                     for a, b, _ in first | second)
                        traces.append({
                            '目標詞': word, '篇章': record['number'], '行號': line_number,
                            '起始位置': body_start + fragment_match.start() + start,
                            '結束位置': body_start + fragment_match.start() + end,
                            '原文': raw, '片段': fragment,
                            '教材優先斷詞': '｜'.join(curriculum),
                            'Jieba斷詞': '｜'.join(jieba),
                            '原因': '候選位於較長詞內，須核對詞界' if inside else '兩斷詞邊界不一致或候選跨越多個詞',
                        })
                    start = fragment.find(word, start + 1)
    if not has_body:
        issues.append(f'分篇待核對：第{record["number"]}篇只有標題或沒有可辨識正文。')
    return certain, suspect, traces, issues


def audit_per_text(text, config, token_views, clean_line, fragment_pattern):
    """Count only full tokens with matching boundaries in both supplied views.

    Missing pieces/settings and unsafe structure have unknown (None) counts.
    Deficit is minimum minus certain count, not a teacher's final ambiguity ruling.
    """
    expected = config['text_count']
    if not isinstance(expected, int) or isinstance(expected, bool) or expected < 1:
        return {'rows': [], 'ambiguities': [], 'issues': ['篇數設定無法辨識。'], 'pieces': []}
    parsed = split_texts(text, expected)
    issues = list(parsed['issues'])
    targets = list(dict.fromkeys(config['targets']))
    supported = [word for word in targets if fragment_pattern.fullmatch(word)]
    invalid_targets = set(targets) - set(supported)
    for word in invalid_targets:
        issues.append(f'目標「{word}」含非單一詞彙片段格式，須教師核對，不作子字串計數。')
    analyses, ambiguities = {}, []
    for piece, record in parsed['assigned'].items():
        result = _piece_counts(record, supported, token_views, clean_line, fragment_pattern)
        certain, suspect, traces, piece_issues = result
        ambiguities.extend(traces)
        issues.extend(piece_issues)
        if not piece_issues:
            analyses[piece] = (certain, suspect)
    rows = []
    for word in targets:
        for piece in range(1, expected + 1):
            minimum = config.get('minimums', {}).get(word, {}).get(piece)
            valid_minimum = isinstance(minimum, int) and not isinstance(minimum, bool) and minimum >= 1
            if not valid_minimum:
                issues.append(f'目標「{word}」第{piece}篇最低次數設定不完整，請核對。')
            known = valid_minimum and word not in invalid_targets and piece in analyses
            if known:
                certain, suspect = analyses[piece]
                count, possible = certain[word], suspect[word]
                deficit = max(minimum - count, 0)
                status = '達標' if count >= minimum else ('待核對' if possible else '未達標')
            else:
                count = possible = deficit = None
                status = '待核對'
            rows.append({'目標詞': word, '篇章': piece,
                         '最低次數': minimum if valid_minimum else None,
                         '確定出現次數': count, '可疑出現次數': possible,
                         '確定不足量': deficit, '狀態': status})
    return {'rows': rows, 'ambiguities': ambiguities,
            'issues': list(dict.fromkeys(issues)), 'pieces': parsed['pieces']}
