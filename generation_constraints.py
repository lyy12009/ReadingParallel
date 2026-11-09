"""Traceable setting arithmetic and non-blocking notices, not teaching thresholds."""
from copy import deepcopy


def _positive_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def summarize_constraints(config, length, context, fragment_pattern):
    """Read current inputs only; never repair them or use a previous text audit.

    A: known setting/format problems. B: teacher review without a calibrated
    criterion. C: semantics/grammar/naturalness cannot be determined here.
    Total requested occurrences is a sum of minima, never distinct-word count.
    """
    result = {
        'rows': [], 'minimums': [], 'issues': [], 'risks': [],
        'context': deepcopy(context),
        'inputs': {'target_config': deepcopy(config), 'length': deepcopy(length)},
        'method': 'settings-summary-v1-no-feasibility-threshold',
        'unassessed': ['語意、語法運用是否正確及故事自然度目前無法自動判定；'
                       '姓名、語法與情境能否自然共存仍須教師審核。'],
    }

    def issue(piece, reason):
        result['issues'].append({'類別': 'A', '篇章': piece, '依據': reason})

    count = config.get('text_count')
    if not _positive_int(count):
        issue(None, f'篇數設定須為正整數，目前為{count!r}；無法建立逐篇摘要。')
        return result
    bounds_valid = (isinstance(length, (list, tuple)) and len(length) == 2
                    and all(_positive_int(v) for v in length) and length[0] <= length[1])
    if not bounds_valid:
        issue(None, f'篇幅範圍須為正整數下限與上限，且下限不大於上限；目前為{length!r}，影響全部{count}篇。')
    lower, upper = length if bounds_valid else (None, None)

    raw_targets = config.get('targets')
    if not isinstance(raw_targets, list) or any(not isinstance(w, str) or not w.strip()
                                               for w in raw_targets):
        issue(None, f'目標詞清單含無法辨識的資料，影響全部{count}篇；詞數與總詞次未知。')
        targets = None
    else:
        targets = list(dict.fromkeys(raw_targets))
        if len(targets) != len(raw_targets):
            issue(None, f'目標詞清單有重複項，影響全部{count}篇；摘要按不同目標詞計一次，原設定不變。')
    matrix = config.get('minimums', {})
    if not isinstance(matrix, dict):
        matrix = {}
    for piece in range(1, count + 1):
        total = 0 if targets is not None else None
        if targets is not None:
            for word in targets:
                values = matrix.get(word, {})
                minimum = values.get(piece) if isinstance(values, dict) else None
                valid = _positive_int(minimum)
                result['minimums'].append({'篇章': piece, '目標詞': word,
                                           '最低次數': minimum if valid else None})
                if not valid:
                    issue(piece, f'第{piece}篇目標「{word}」最低次數缺漏或不符現有正整數設定格式'
                                 f'（目前為{minimum!r}）；最低總詞次未知，不以0替代。')
                    total = None
                elif total is not None:
                    total += minimum
                if not fragment_pattern.fullmatch(word):
                    issue(piece, f'第{piece}篇目標「{word}」不符合既有正式檢測的單一詞彙片段格式；'
                                 '數量仍依設定列示，詞界須核對，不自行改詞或拆分。')
        result['rows'].append({
            '篇章': piece, '目標詞數量': len(targets) if targets is not None else None,
            '最低總詞次': total, '篇幅下限': lower, '篇幅上限': upper})
        words_text = str(len(targets)) if targets is not None else '未知'
        total_text = str(total) if total is not None else '未知'
        length_text = f'{lower}–{upper}個中文字' if bounds_valid else '未知'
        result['risks'].append({
            '類別': 'B', '篇章': piece,
            '說明': f'第{piece}篇：不同目標詞{words_text}個、最低總詞次{total_text}次、篇幅{length_text}。'
                    '這些是設定數量，不是教學合理性結論；與人名、語法等條件合用的可行性待教師評估。'
                    '尚無經教師確認的詞數／詞次密度判準，不判定一定衝突，'
                    '也不保證自然或達標。可調整設定，亦可原樣繼續生成。'})
    return result
