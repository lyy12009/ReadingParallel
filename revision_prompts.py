"""Pure revision planning from current formal per-text audit snapshots.

No tokenizer, substring counts, teaching thresholds or semantic adjudication.
Prompts are rebuilt from current inputs, never cached as approved results.
"""


def revision_plan(report, config, text, audited_text, signature, audited_signature):
    """Reject stale/incomplete rows; separate explicit deficits from review items."""
    invalid = {'valid': False, 'deficits': [], 'reviews': [], 'issues': [],
               'all_met': False, 'requirements': []}
    if (not isinstance(report, dict) or not signature or signature != audited_signature
            or text != audited_text):
        return invalid
    expected = {(word, piece): config.get('minimums', {}).get(word, {}).get(piece)
                for word in config['targets']
                for piece in range(1, config['text_count'] + 1)}
    rows = report.get('rows')
    if not isinstance(rows, list) or len(rows) != len(expected):
        return invalid
    seen, deficits, reviews = set(), [], []
    for row in rows:
        key = (row.get('目標詞'), row.get('篇章'))
        if key not in expected or key in seen or row.get('最低次數') != expected[key]:
            return invalid
        seen.add(key)
        minimum = row.get('最低次數')
        count, possible, deficit = (row.get(k) for k in
                                   ('確定出現次數', '可疑出現次數', '確定不足量'))
        status = row.get('狀態')
        if count is None and possible is None and deficit is None and status == '待核對':
            reviews.append(dict(row))
            continue
        if (not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 1
                or any(not isinstance(v, int) or isinstance(v, bool) or v < 0
                       for v in (count, possible, deficit))
                or deficit != max(minimum - count, 0)):
            return invalid
        expected_status = ('達標' if count >= minimum else
                           ('待核對' if possible else '未達標'))
        if status != expected_status:
            return invalid
        if status == '未達標':
            deficits.append({k: row[k] for k in
                             ('篇章', '目標詞', '最低次數', '確定出現次數', '確定不足量')})
        if status == '待核對' or possible:
            reviews.append(dict(row))
    return {'valid': True, 'deficits': deficits, 'reviews': reviews,
            'requirements': [{k: r[k] for k in ('目標詞', '篇章', '最低次數')} for r in rows],
            'issues': list(report.get('issues', [])),
            'all_met': bool(rows) and all(r['狀態'] == '達標' for r in rows)
                       and not report.get('issues')}


def revision_prompt(plan, text, context, notes):
    """Only definite deficits request additions; pending-only reports get no prompt."""
    if not plan['valid'] or plan['issues'] or not (plan['deficits'] or plan['all_met']):
        return None
    targeted = bool(plan['deficits'])
    mode = '針對性複現修訂' if targeted else '一般文字修訂（沒有明確複現不足）'
    lines = [f'【修訂模式】{mode}',
             '請修訂下列華語閱讀文本。保留故事主要事件、人物關係及結果；不續寫參考課文。',
             '【待修訂文本（含未要求修改的篇章）】', text]
    if targeted:
        lines += ['【Python正式不足清單：僅下列篇章與目標詞需要補足】']
        for row in plan['deficits']:
            lines.append(f"第{row['篇章']}篇｜{row['目標詞']}｜最低{row['最低次數']}次"
                         f"｜確定{row['確定出現次數']}次｜至少還缺{row['確定不足量']}次")
        lines += ['優先在原有上下文自然補足，必要時可修改鄰近句子；不機械重複詞語，'
                  '不為詞次加入不自然的句子。不任意修改已達標篇章，'
                  '也不要減少原有確定詞次或降低任何原設定。']
    else:
        lines += ['目前選定目標的確定詞次均符合最低要求；不得虛構不足。'
                  '這不代表語意、自然度或整體教學品質已通過。'
                  '僅依教師意見作必要文字修訂，保留既有詞次與原設定。']
        lines += ['【原最低要求（保留，不是缺額）】']
        for row in plan['requirements']:
            lines.append(f"{row['目標詞']}：第{row['篇章']}篇至少{row['最低次數']}次")
    if plan['reviews'] or plan['issues']:
        lines += ['【待核對限制】另有歧義或未知篇章，須教師人工核對；'
                  '它們不屬於正式不足清單，不以可疑次數充當確定詞次，'
                  '不自動要求補寫待核對詞，也不補造漏篇。']
    c = context
    lines += ['【保留原生成條件】',
              f"{c['book']}{c['lesson']}；{c['form']}，共{c['text_count']}篇，{c['relation']}；"
              f"每篇{c['length'][0]}–{c['length'][1]}個中文字（不含標題與說話者）。",
              '優先使用截至本課已學詞（教材生詞、課文重建斷詞與語法證據）；'
              '必要新詞盡量少，使用臺灣繁體中文。',
              '目標語法：' + ('；'.join(c['grammar']) or '原課文未載') + '。',
              '核准人名／說話者：' + ('、'.join(c['names']) or '無；不使用人名') + '；不可自行新增。']
    if c['theme']:
        lines += ['教師設定情境：' + c['theme']]
    if c['guides']:
        lines += ['核准引導詞：' + '、'.join(c['guides']) +
                  '；保留引導意義，不把僅作引導的詞自動加為複現目標。']
    if c['allowed']:
        lines += ['教師一般允許詞：' + '、'.join(c['allowed'])]
    if c['retained']:
        lines += ['教師保留的補充詞：' + '、'.join(c['retained']) + '；不自動當成已學。']
    lines += ['【教師修訂意見（原樣保留，不覆寫設定）】', notes or '未另指定。',
              '教師意見若與最低次數、篇數、篇幅或其他原設定衝突，'
              '請提醒教師核對，不自行降低要求、刪目標或增加篇幅。',
              '【輸出】僅輸出完整修訂文本，保持原篇數與篇章編號；'
              '輸出前自行核對，不輸出逐項分析或檢測報告。'
              '貼回後必須重新執行 Python 正式檢測，再由教師審核定稿；'
              'LLM自稱通過不能取代檢測。']
    return '\n'.join(lines)
