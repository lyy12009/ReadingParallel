# -*- coding: utf-8 -*-
"""ReadingParallel Python v1.3: independent teacher-in-the-loop LLM prompt + audit app."""
from __future__ import annotations
import io
import json
import re
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

import jieba
import streamlit as st
from docx import Document
from docx.shared import Pt, Cm

HERE = Path(__file__).resolve().parent
DATA = json.loads((HERE / 'corpus.json').read_text(encoding='utf-8'))

# 專案既有繁體大字典優先：GitHub repo 根目錄，其次 dictionaries/。
# 不在雲端動態下載，也不假定字典位於 jieba 套件內部。
DICT_CANDIDATES = [HERE / 'dict.txt.big', HERE / 'dictionaries' / 'dict.txt.big']
BIG_DICT = next((p for p in DICT_CANDIDATES if p.is_file() and p.stat().st_size > 0), None)
USER_DICT = HERE / 'dictionaries' / 'teaching_words.txt'

@st.cache_resource(show_spinner='檢查並初始化中文斷詞字典…')
def build_tokenizer(dict_path: str, userdict_path: str, dict_mtime: float):
    """Do not allow a malformed uploaded dict.txt.big to crash the entire app.

    Valid entries: WORD INTEGER_FREQUENCY [POS]. No silent success badge on fallback.
    """
    diagnostics = []
    chosen = None
    if dict_path:
        source = Path(dict_path)
        invalid = []
        valid = 0
        # Some dictionaries copied from the web contain BOM, blank/comment lines,
        # malformed frequencies, or even HTML. Validate *before* Jieba loads it.
        import tempfile
        import hashlib
        digest = hashlib.sha256(str(source.resolve()).encode()).hexdigest()[:12]
        clean_path = Path(tempfile.gettempdir()) / f'readingparallel_jieba_{digest}_{int(dict_mtime)}.txt'
        try:
            if not clean_path.exists():
                with source.open('r', encoding='utf-8-sig') as src, clean_path.open('w', encoding='utf-8') as dst:
                    for lineno, raw in enumerate(src, 1):
                        row = raw.strip()
                        if not row or row.startswith('#'):
                            continue
                        cols = row.split()
                        if len(cols) not in (2, 3) or not cols[0] or not cols[1].isdigit():
                            if len(invalid) < 5:
                                invalid.append((lineno, row[:90]))
                            continue
                        dst.write(' '.join(cols) + '\n')
                        valid += 1
            else:
                # Cached normalized file for current source mtime
                with clean_path.open('r', encoding='utf-8') as fh:
                    valid = sum(1 for _ in fh)
            if invalid:
                diagnostics.append(f'大字典有不符合 Jieba 格式的資料列，已略過。範例：{invalid}')
            if valid < 1000:
                diagnostics.append(f'大字典只有 {valid} 筆有效詞條，可能上傳了網頁 HTML 或錯誤檔案；已改用預設字典。')
            else:
                chosen = str(clean_path)
        except (OSError, UnicodeError, ValueError) as e:
            diagnostics.append(f'讀取繁體大字典失敗：{type(e).__name__}: {str(e)[:180]}。改用預設字典。')

    try:
        tok = jieba.Tokenizer(dictionary=chosen)
        tok.initialize()
    except Exception as e:
        diagnostics.append(f'Jieba 初始化失敗：{type(e).__name__}: {str(e)[:160]}。改用預設字典。')
        chosen = None
        tok = jieba.Tokenizer()
        tok.initialize()
    if userdict_path:
        try:
            tok.load_userdict(userdict_path)
        except Exception as e:
            diagnostics.append(f'自訂教學字典載入失敗：{type(e).__name__}: {str(e)[:120]}')
    return tok, bool(chosen), diagnostics

TOKENIZER, BIG_DICT_LOADED, DICT_WARNINGS = build_tokenizer(
    str(BIG_DICT) if BIG_DICT else '',
    str(USER_DICT) if USER_DICT.is_file() else '',
    BIG_DICT.stat().st_mtime if BIG_DICT else 0.0,
)

PARTS = ['對話1', '對話2', '文章1', '文章2']
HAN_RE = re.compile(r'[\u3400-\u9fff]')
CJK_WORD = re.compile(r'[\u3400-\u9fffA-Za-z0-9]+')
PUNCT = re.compile(r'[，。！？；：「」『』、,.!?;:\s]+')

st.set_page_config(page_title='高複現閱讀文本工作室', page_icon='📖', layout='wide')
st.title('高複現華語閱讀文本工作室 · Python v1.3')
st.caption('教師選課 → 生成新文本 → Python 檢測 → 選擇性修訂 → 依定稿文本命題 → 檢核 → Word。LLM 請在外部使用，無需 API Key。')
if BIG_DICT_LOADED:
    st.success(f'繁體中文大字典已載入：{BIG_DICT.relative_to(HERE)}（{BIG_DICT.stat().st_size/1024/1024:.1f} MB）')
else:
    st.warning('繁體中文大字典未成功載入，目前使用 Jieba 預設字典；程式仍可執行。請檢查 dict.txt.big 是否為「原始文字」而非 GitHub 網頁。')
for warning in DICT_WARNINGS:
    st.warning(warning)



def uniq(xs):
    return list(dict.fromkeys(str(x).strip() for x in xs if str(x).strip()))


def words(d, parts=PARTS):
    return uniq(w for p in parts for w in d.get(p + '生詞', []))


def grammar(d, parts=PARTS):
    return uniq(w for p in parts for w in d.get(p + '語法點', []))


def cleanbody(t):
    lines=[]
    for line in t.strip().splitlines():
        if re.match(r'^\s*(?:【文本[一二三123]】|文本[一二三123]|教師備註|字數|複現率|來源|語法證據)',line):
            continue
        line=re.sub(r'^\s*[^：:\n]{1,12}[：:]\s*', '',line)
        lines.append(line)
    return '\n'.join(lines)


def count_han(t):
    return len(HAN_RE.findall(cleanbody(t)))


def ngram_overlap(a,b,n=8):
    a=''.join(HAN_RE.findall(cleanbody(a)))
    b=''.join(HAN_RE.findall(cleanbody(b)))
    if not a or not b: return 0,[]
    chunks=uniq(a[i:i+n] for i in range(max(0,len(a)-n+1)))
    found=[c for c in chunks if c in b]
    return len(found)/max(len(chunks),1),found[:12]


def normalize_text(t):
    return ''.join(HAN_RE.findall(cleanbody(t)))


def get_lexicon(upto):
    known=set()
    for d in DATA[:upto+1]:
        known.update(words(d))
    return known


def _curriculum_segment(fragment, by_first):
    """教材詞庫優先的最長比對；剩餘片段交 Jieba 斷詞。
    避免以已學單字拼出未教過的雙字詞，故已學單字不參與優先比對。
    """
    out, buf, i = [], [], 0
    def flush():
        if buf:
            out.extend(t for t in TOKENIZER.lcut(''.join(buf), HMM=False) if t.strip())
            buf.clear()
    while i < len(fragment):
        match = next((w for w in by_first.get(fragment[i], ()) if fragment.startswith(w, i)), None)
        if match:
            flush(); out.append(match); i += len(match)
        else:
            buf.append(fragment[i]); i += 1
    flush()
    return out


def audit(text,known,target,original,allow):
    baseline=set(known)|set(allow)
    # 優先使用教材已學詞彙配合繁體大字典；局部 tokenizer 避免不同使用者互相污染。
    # 第一次初始化採專案字典，逐份文本用同一個全局只讀詞庫。
    normalized=cleanbody(text)
    fragments=CJK_WORD.findall(normalized)
    by_first = {}
    for word in sorted((w for w in baseline if len(w)>1 and CJK_WORD.fullmatch(w)),key=len,reverse=True):
        by_first.setdefault(word[0], []).append(word)
    tokens=[]
    for f in fragments:
        tokens.extend(_curriculum_segment(f, by_first))
    # Lexicon-based greedy scan prioritizes explicit known multi-character words.
    # Does not infer that individual known characters license a new compound word.
    unknown=Counter()
    counts={w:len(re.findall(re.escape(w), normalized)) for w in target}
    known_set=baseline | set('的一了是不我你他她們嗎呢啊有在和也都很去來說要會')
    for w in tokens:
        if HAN_RE.search(w) and w not in known_set:
            unknown[w]+=1
    overlap,examples=ngram_overlap(original,text)
    old=normalize_text(original); new=normalize_text(text)
    sim=SequenceMatcher(None,old,new,autojunk=False).ratio() if old and new else 0
    return {'字數':count_han(text),'目標詞複現':counts,'疑似超綱':unknown,'疑似超綱詞次':sum(unknown.values()),
            '全文中文字詞次':sum(1 for w in tokens if HAN_RE.search(w)),
            '原文連續片段重合率':overlap,'整體字元相似度':sim,'重合片段':examples}


def format_prompt(text):
    st.code(text, language='text')
    st.caption('按程式碼框右上角複製，再貼至 ChatGPT、Gemini 或其他 LLM。')


def compare_changes(original, revised, limit=12):
    """Textual change candidates only; the model/teacher must assess semantic relevance."""
    def chunks(t):
        return [v.strip() for v in re.split(r'(?<=[。！？!?；;\n])|(?<=，)', t) if v.strip()]
    before, after = chunks(original), chunks(revised)
    sm = SequenceMatcher(None, before, after, autojunk=False)
    changes=[]
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag=='equal':
            continue
        prev=''.join(before[i1:i2]).strip()[:145]
        next_=''.join(after[j1:j2]).strip()[:145]
        if not next_:
            continue
        # Do not equate a text diff with a factual/semantic difference.
        changes.append({'原文片段':prev or '（原文無對應片段）', '新文本片段':next_, '類型':tag})
    if not changes and original.strip()!=revised.strip():
        changes=[{'原文片段':original[:145], '新文本片段':revised[:145], '類型':'文字變動'}]
    return changes[:limit]


def glossary_lines(text):
    """Teacher input: 詞語：說明, one item per line; no invented definitions."""
    rows=[]
    for line in text.splitlines():
        line=line.strip()
        if not line: continue
        parts=re.split('[：:｜|]',line,maxsplit=1)
        word=parts[0].strip()
        meaning=parts[1].strip() if len(parts)>1 else ''
        if word:
            rows.append((word,meaning))
    return rows


def write_docx(text, quiz, answer, title, glossary_text='', teacher_version=False):
    doc=Document()
    sec=doc.sections[0]
    sec.top_margin=sec.bottom_margin=Cm(2.2)
    sec.left_margin=sec.right_margin=Cm(2.3)
    styles=doc.styles
    normal=styles['Normal']
    normal.font.name='Microsoft JhengHei'
    normal.font.size=Pt(13)
    normal.paragraph_format.line_spacing=1.5
    normal.paragraph_format.space_after=Pt(4)
    doc.add_heading(title,0)
    items=glossary_lines(glossary_text)
    if items:
        doc.add_heading('閱讀前補充生詞',level=1)
        for word,meaning in items:
            doc.add_paragraph(f'{word}：{meaning}' if meaning else word)
    doc.add_heading('閱讀文本',level=1)
    for line in text.strip().splitlines():
        doc.add_paragraph(line)
    if quiz.strip():
        doc.add_heading('閱讀測驗',level=1)
        for line in quiz.strip().splitlines():
            doc.add_paragraph(line)
    if teacher_version and answer.strip():
        doc.add_page_break()
        doc.add_heading('教師答案',level=1)
        for line in answer.strip().splitlines():
            doc.add_paragraph(line)
    buf=io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def split_items(text):
    """Count actual question headers, ignoring choices and sample placeholders."""
    matches=[]
    for line in text.splitlines():
        val=line.strip()
        if re.match(r'^(?:\d{1,2}[.．、)]|[（(]\d{1,2}[)）]|第[一二三四五六七八九十\d]+題)\s*',val):
            if len(re.sub(r'^(?:\d{1,2}[.．、)]|[（(]\d{1,2}[)）]|第[一二三四五六七八九十\d]+題)\s*','',val).strip())>=3 and not re.search(r'\[.*?題.*?\]|\[第一題',val):
                matches.append(val)
    return matches


def inspect_questions(raw, qtype, qty):
    main=re.split(r'教師答案\s*[:：]?\s*',raw,maxsplit=1)[0]
    found=split_items(main)
    issues=[]
    if len(found)!=qty:
        issues.append(f'題數應為 {qty}，目前辨識 {len(found)} 題。')
    if re.search(r'【?對話[一二三123]】?|詞彙來源|第[一二三123]冊第',main):
        issues.append('出現疑似對話續寫或詞彙來源說明。')
    if re.search(r'\[(?:第一題|選項|答案|題幹)|依此編寫|填寫題目|請直接給',main):
        issues.append('內容可能保留提示詞範例，而非正式測驗題。')
    if qtype=='單選題':
        for letter in 'ABCD':
            count=len(re.findall(r'(?m)^\s*'+letter+r'[.．、)）]\s*\S+',main))
            if count<qty:
                issues.append(f'單選題選項 {letter} 只有 {count}/{qty} 項。')
    return found, issues


def extract_quiz_language(quiz):
    # Separate questions and options; omit headings, question numbers and A-D labels.
    heads, choices=[], []
    for raw in quiz.splitlines():
        line=raw.strip()
        if not line or re.fullmatch(r'(閱讀測驗|閱讀理解|問題|選擇題|是非題|簡答題|事件排序|教師答案|答案)[:：]?',line):
            continue
        if re.match(r'^(?:A|B|C|D|Ａ|Ｂ|Ｃ|Ｄ)[.．、)）\s]',line,re.I):
            choices.append(re.sub(r'^(?:A|B|C|D|Ａ|Ｂ|Ｃ|Ｄ)[.．、)）\s]*','',line,flags=re.I))
        else:
            line=re.sub(r'^(?:\d{1,2}[.．、)）]|[（(]\d{1,2}[)）]|第[一二三四五六七八九十\d]+題)\s*','',line)
            heads.append(line)
    return '\n'.join(heads), '\n'.join(choices)


def audit_quiz_language(quiz, known, allow):
    question_stems, choices=extract_quiz_language(quiz)
    r1=audit(question_stems,known,[], '',allow)
    r2=audit(choices,known,[], '',allow)
    return {'題幹與說明':r1,'選項':r2}


def quiz_revision_prompt(quiz,issues,stems_bad,choices_bad,final,quiz_type,quiz_qty,retain):
    return f'''你是臺灣對外華語閱讀測驗的審題教師。只修改下列題幹、作答說明與選項，不得生成新故事，也不得修改閱讀文本。
【已定稿的閱讀文本，僅用於核對答案】
{final}
【原測驗題目】
{quiz}
【程式檢查結果（斷詞誤判可能存在，先依語意確認）】
形式問題：{'；'.join(issues) or '無'}
題幹／說明疑似超綱：{'、'.join(stems_bad) or '無'}
選項疑似超綱：{'、'.join(choices_bad) or '無'}
教師明確允許保留的測驗用詞：{'、'.join(retain) or '無'}
【修改規則】
1. 僅在必要時改寫含疑似超綱詞的題幹、作答說明與選項；請盡量改用較初級、已學過的詞語。已允許的用詞可以保留。
2. 不改變原本要考的閱讀資訊，不增加定稿文本沒有的事實；不能把閱讀本文中的重要差異刪到失去測驗目的。
3. 以「{quiz_type}」輸出 {quiz_qty} 題，題號由 1. 起。若是單選題每題必有 A、B、C、D 四個完整選項且僅一個正確。
4. 輸出「閱讀測驗」及完整題目，再輸出「教師答案」及每題答案。不要提供解析、詞彙來源、出處或對話二。
只輸出可直接給教師使用的正式題目與答案。'''


with st.sidebar:
    st.header('教材與生成設定')
    book=st.selectbox('冊別',uniq(d['冊'] for d in DATA))
    choices=[(i,d) for i,d in enumerate(DATA) if d['冊']==book]
    ix=st.selectbox('課別',list(range(len(choices))),format_func=lambda j:choices[j][1]['課'])
    idx,lesson=choices[ix]
    available=[p for p in PARTS if lesson.get(p,'').strip()]
    reference=st.selectbox('複現目標（來源文本）',available)
    form=st.selectbox('新文本形式',['對話','閱讀短文'])
    n=st.selectbox('生成篇數',[1,2,3])
    relation=st.selectbox('多篇關係',['各篇獨立','連續故事']) if n>1 else '單篇'
    whole='\n'.join(lesson.get(p,'') for p in PARTS)
    whole_len=count_han(whole)
    length=st.selectbox('每篇字數',['依整課課文總長度','100–150','200–300','400–500'])
    target_len={'依整課課文總長度':(max(30,round(whole_len*.8)),max(40,round(whole_len*1.2))), '100–150':(100,150),'200–300':(200,300),'400–500':(400,500)}[length]
    cover=st.selectbox('本次目標詞覆蓋需求',['自然度優先','每篇至少60%','每篇至少80%'])
    theme=st.text_input('新情境／主題（選填）', placeholder='例如：從機場接人改為在校園第一次見面')
    allowed_raw=st.text_area('教師允許保留的額外詞（可選，每行一詞）',height=75)
    allowed=uniq(re.split(r'[,，、\n]+',allowed_raw))
    st.divider()
    st.subheader('閱讀測驗預先設定')
    quiz_basis=st.selectbox('命題依據',['優先針對新舊文本變化','依閱讀理解目標','兩者混合'],index=0)
    quiz_type=st.selectbox('題型',['單選題','是非題','簡答題','事件排序','混合題型'])
    quiz_qty=st.selectbox('題數',[3,4,5,6],index=0)
    quiz_focus=st.selectbox('閱讀理解重點',['訊息擷取與理解','推論與整合','混合'])
    quiz_extra=st.text_area('教師指定命題方向（選填）',placeholder='例如：人物改變決定的原因、結局的差異',height=65)


source=lesson[reference]; target=words(lesson,[reference]); gtarget=grammar(lesson,[reference])
known=get_lexicon(idx)
current_key=f'{idx}:{reference}:{form}:{n}'
if st.session_state.get('active_key')!=current_key:
    st.session_state['active_key']=current_key
    for k in ['draft','final_text','quiz_text','answer_text','approved','last_audit','audited_text','retained_words','glossary_editor','quiz_allowed_raw']:
        st.session_state.pop(k,None)

with st.expander('查看教材來源與複現目標',expanded=False):
    st.write('**原課文：**');st.text(source)
    st.write('**當課目標生詞：**','、'.join(target) or '（原文未載）')
    st.write('**當課目標語法：**','；'.join(gtarget) or '（原文未載）')
    st.caption(f'截至本課累積生詞：{len(known)} 項；整課課文字數約 {whole_len} 字。來源文本可能有人名或教材編排瑕疵，須由教師核對。')

st.subheader('第一階段｜生成真正的新文本')
with st.expander('生成提示詞',expanded=True):
    prompt1=f'''你是臺灣對外華語教材編寫者。請生成{n}篇**新的**{form}，供「{book} {lesson['課']}」學習者複習。
你必須創作新的事件，不是重寫、複製、換句話說、延長或續寫原課文。
【原課文（僅供理解教學情境，禁止照搬）】\n{source}
【任務】
1. 新文本必須有明確不同於原課文的事件目的、重要資訊或結果；不能只更換人物姓名、地點或對話順序。
2. 可以沿用原課文人物，但避免創造未曾出現的人名。{('指定新情境：'+theme) if theme else '請自行提出有變化的新情境。'}
3. 每篇正文繁體中文（臺灣用語），長度約 {target_len[0]} 至 {target_len[1]} 個中文字，不含標題與說話者標籤。
4. {('每篇需有相對完整的小事件，且篇幅相近；'+relation) if n>1 else '單篇需有完整的小事件。'}
5. 本課應複現的生詞：{'、'.join(target)}。
6. 本課應複現的語法：{'；'.join(gtarget) or '（原文未載）'}。
7. {cover}；在自然、正確的前提下盡可能高覆蓋，不要機械堆疊詞語。
8. 優先使用截至本課已學詞彙；如果必要使用新詞，保持最少。教師稍後會用 Python 檢測。
【嚴格輸出格式】
只輸出「【文本一】標題」及正文；多篇依序為「【文本二】」「【文本三】」。不得再複製原課文，不要生成測驗題、詞語解釋、詞彙來源表、語法分析或檢測報告。'''
    format_prompt(prompt1)

st.subheader('第二階段｜貼回文本、Python 檢測、修訂')
st.text_area('將 LLM 產生的閱讀文本貼在這裡（可直接修改）',height=280,key='draft')
if st.button('執行 Python 檢測',type='primary'):
    raw=st.session_state.get('draft','').strip()
    if not raw:
        st.warning('請先貼入文本。')
    else:
        report=audit(raw,known,target,source,allowed)
        st.session_state['last_audit']=report
        st.session_state['audited_text']=raw
        st.session_state['approved']=False
report=st.session_state.get('last_audit')
if report:
    c1,c2,c3,c4=st.columns(4)
    c1.metric('正文中文字數',report['字數'])
    c2.metric('目標詞至少出現一次',f"{sum(bool(v) for v in report['目標詞複現'].values())}/{len(target)}")
    c3.metric('疑似超綱詞次',report['疑似超綱詞次'])
    c4.metric('與原文相似度',f"{report['整體字元相似度']:.0%}")
    st.caption('以上為程式文字檢測，不是教學效度驗證。超綱結果可能受分詞、人名、課文已學但未列生詞及語法例句影響。')
    if report['原文連續片段重合率']>.25 or report['整體字元相似度']>.7:
        st.error('與原課文的相似度偏高：請人工確認是否只是在照搬、少量改寫或延續原對話。')
    if not(target_len[0]<=report['字數']<=target_len[1]*n):
        st.info('篇幅提醒：請逐篇檢查是否符合每篇字數範圍；目前顯示總正文概估字數。')
    left,right=st.columns(2)
    with left:
        st.markdown('**目標詞複現次數**')
        st.dataframe([{'生詞':w,'次數':v} for w,v in report['目標詞複現'].items()],hide_index=True,use_container_width=True)
    with right:
        st.markdown('**疑似超綱詞（需教師核對）**')
        st.dataframe([{'詞':w,'次數':v} for w,v in report['疑似超綱'].most_common()],hide_index=True,use_container_width=True)
    st.markdown('**保留少量超綱詞，作為閱讀前補充生詞**')
    st.caption('大量閱讀可以接觸少量尚未教過、但有助於理解的詞。勾選保留不代表已學；教師可在下方為學生提供簡短解釋。')
    suspect_options=list(report['疑似超綱'].keys())
    if 'retained_words' not in st.session_state:
        st.session_state['retained_words']=[]
    st.session_state['retained_words']=[w for w in st.session_state['retained_words'] if w in suspect_options]
    retained=st.multiselect('教師決定保留的疑似超綱詞（不要求 LLM 刪除）',suspect_options,key='retained_words')
    if retained:
        st.caption('下列詞語解釋可稍後放在學生閱讀文本前；請由教師填寫，系統不會自動捏造詞義。')
    # Display selected vocabulary as explicit candidates; explanations remain teacher-written.
    with st.expander('補充生詞說明（可於第四階段編輯）',expanded=bool(retained)):
        st.code('\n'.join(f'{w}：' for w in retained) if retained else '目前尚未選擇保留詞',language='text')
    with st.expander('生成修訂提示詞（選用）'):
        notes=st.text_area('教師修訂意見',placeholder='例如：保留人物，但改變事件；增加當課未使用詞；調整自然度。')
        suspects='、'.join(f'{w}({c})' for w,c in report['疑似超綱'].most_common(45)) or '未檢出'
        lack='、'.join(w for w,c in report['目標詞複現'].items() if not c) or '無'
        to_repair='、'.join(f'{w}({c})' for w,c in report['疑似超綱'].most_common(45) if w not in retained) or '無'
        prompt2=f'''請只修訂下列已生成的華語閱讀文本，不要寫新篇章，不要續寫原課文。
【教師意見】{notes or '優先處理疑似超綱並維持語句自然。'}
【原教材來源，僅供避免照搬】\n{source}
【本次待修訂文本】\n{st.session_state['draft']}
【Python 檢測（疑似而非確定錯誤）】\n疑似超綱：{suspects}\n教師確認保留、供閱讀前補充的詞：{'、'.join(retained) or '無'}\n優先檢查是否需要改寫的疑似超綱詞：{to_repair}\n未出現目標生詞：{lack}\n與原文相似度：{report['整體字元相似度']:.0%}
【要求】教師指定保留的詞不得因為超綱而刪除；其餘疑似超綱詞請逐項判斷是否需要修改（含人名及語境），必要時改為截至本課學過的詞；不能只把超綱詞拆成單字，不能犧牲語意正確或自然度。維持原先篇數、各篇完整事件、{target_len[0]}至{target_len[1]}字左右。若原課文重合過高，請改變事件目的、資訊或結果。只輸出修訂後的完整文本，禁止輸出詞彙來源、對話分析、解釋或修訂報告。'''
        format_prompt(prompt2)
    st.markdown('**教師確認定稿**')
    st.caption('檢測後如要人工修改，請重新檢測。確認前不能進行命題。')
    if st.session_state.get('audited_text') != st.session_state.get('draft'):
        st.warning('文本已在上次檢測後修改，請先重新檢測。')
    if st.button('將目前檢測內容確認為定稿', disabled=st.session_state.get('audited_text') != st.session_state.get('draft')):
        st.session_state['final_text']=st.session_state['draft']
        st.session_state['approved']=True
        st.session_state['quiz_text']=''
        st.session_state['answer_text']=''
        st.session_state.pop('answer_editor', None)
        st.session_state.pop('quiz_answer_source', None)
        st.session_state['approved_retained']=list(st.session_state.get('retained_words',[]))
        st.success('已鎖定此份文本作為命題依據。')

st.divider()
st.subheader('第三階段｜依定稿文本命題')
if not st.session_state.get('approved'):
    st.info('請先在第二階段完成檢測，並按「確認為定稿」。')
else:
    final=st.session_state['final_text']
    changes=compare_changes(source,final)
    st.markdown('**本次命題設定（可回到左側選單修改）**')
    st.write(f'命題依據：{quiz_basis}｜題型：{quiz_type}｜題數：{quiz_qty}｜閱讀理解：{quiz_focus}')
    st.caption('下列只是程式比對的文字差異，不代表語意、事件一定已發生變化；應由教師與 LLM 依全文確認。')
    with st.expander('檢視原課文與新文本的文字變化線索',expanded=True):
        if changes:
            st.dataframe([{'原課文片段':c['原文片段'],'新文本片段':c['新文本片段']} for c in changes],hide_index=True,use_container_width=True)
        else:
            st.info('沒有辨識出明顯的文字差異；請確認不是照搬原課文。')
    difference_text='\n'.join(f"- 原：{v['原文片段']}\n  新：{v['新文本片段']}" for v in changes) or '（未辨識到明顯變化）'
    if quiz_basis=='依閱讀理解目標':
        difference_rule='不必刻意比較原課文，依定稿閱讀文本及指定的理解目標命題。'
        difference_section=''
    else:
        difference_rule=('優先從新文本中與原課文不同的重要資訊、人物決定、事件發展或結果命題；只考新文本中的內容，不要求學生讀過原課文。' if quiz_basis=='優先針對新舊文本變化' else '兼顧新文本的關鍵變化與一般閱讀理解，兩類題目皆須涵蓋。')
        difference_section='【Python 提供的文字差異線索（僅供參考，不保證是重要情節）】\n'+difference_text
    prompt3=f'''你是臺灣華語閱讀測驗命題教師。此任務**只命題，不生成、改寫或續寫任何閱讀文本**。
【唯一可供學生作答的閱讀文本（教師定稿）】
{final}
{difference_section}
【命題設定】
題型：{quiz_type}；題數：{quiz_qty}；理解目標：{quiz_focus}。
教師指定命題方向：{quiz_extra or '無'}。
命題原則：{difference_rule}
1. 每題必須有明確答案，且答案能由「定稿文本」支持；不得單憑原課文出題。
2. 原文與新文本的比較只供命題者挑選新文本的重要訊息，**學生不需要知道原課文**，題幹不可問「和原文相比」。
3. 避免無關詞語釋義、出處、課別、語法術語；使用學生能理解的繁體中文與臺灣用語。
4. 單選題每題四選項（A/B/C/D），只有一項正確；是非題要能判斷對錯；簡答與排序題須有可驗證答案。
5. 不得提供只有題號的空白模板，也不得輸出新對話、「對話二」、新篇章、詞彙出處、命題原理或教師備註。
【輸出要求】
第一段標題固定「閱讀測驗」，逐題以「1.」「2.」連續編號至第 {quiz_qty} 題，**完整寫出每題題幹與必要選項**。
第二段標題固定「教師答案」，按題號列出簡短標準答案；簡答題可附接受答案的條件。
請直接輸出正式測驗與答案，不要重印閱讀文本。'''
    format_prompt(prompt3)
    quiz_raw=st.text_area('貼回 LLM 命題結果（可直接修改）',height=250,key='quiz_text')
    quiz_student, *quiz_answer = re.split(r'教師答案\s*[:：]?\s*',quiz_raw,maxsplit=1)
    if st.session_state.get('quiz_answer_source')!=quiz_raw:
        st.session_state['answer_editor']=quiz_answer[0].strip() if quiz_answer else st.session_state.get('answer_editor','')
        st.session_state['quiz_answer_source']=quiz_raw
    output_answer=st.text_area('教師答案（可貼入或修改；學生版不會印出）',height=110,key='answer_editor')
    found,issues=inspect_questions(quiz_student,quiz_type,quiz_qty)
    st.markdown('**測驗題幹、作答說明與選項的超綱檢測**')
    st.caption('閱讀正文可以保留經教師同意的超綱詞；測驗文字須另行檢測。題幹與選項分開統計，並非以閱讀正文是否通過代替。')
    quiz_allowed_raw=st.text_area('測驗專用允許詞（每行一詞；選填）',height=65,key='quiz_allowed_raw',placeholder='例如：已在閱讀前補充且學生能理解的必要詞')
    quiz_allowed=uniq(re.split(r'[,，、\n]+',quiz_allowed_raw))
    quiz_language=audit_quiz_language(quiz_student,known,allowed+quiz_allowed)
    stems_bad=quiz_language['題幹與說明']['疑似超綱']
    choices_bad=quiz_language['選項']['疑似超綱']
    if quiz_student.strip():
        c_stem,c_choice=st.columns(2)
        with c_stem:
            st.metric('題幹／說明疑似超綱詞次',sum(stems_bad.values()))
            if stems_bad:
                st.dataframe([{'疑似超綱詞':w,'次數':c} for w,c in stems_bad.most_common()],hide_index=True,use_container_width=True)
            else:
                st.success('目前未標記題幹或說明的疑似超綱詞。')
        with c_choice:
            st.metric('選項疑似超綱詞次',sum(choices_bad.values()))
            if choices_bad:
                st.dataframe([{'疑似超綱詞':w,'次數':c} for w,c in choices_bad.most_common()],hide_index=True,use_container_width=True)
            else:
                st.success('目前未標記選項的疑似超綱詞。')
        st.caption('此為教材詞庫與 Jieba 的比對警示，可能包含常用功能詞、人名及斷詞誤判；教師可修改題目或於上方允許詞欄確認保留。')
        with st.expander('複製：測驗題目專用修訂 Prompt'):
            format_prompt(quiz_revision_prompt(quiz_student,issues,list(stems_bad),list(choices_bad),final,quiz_type,quiz_qty,quiz_allowed))
    if st.button('檢查題目完整性與語言難度'):
        if stems_bad or choices_bad:
            st.warning('題幹或選項有疑似超綱詞，請核對上方檢測結果。')
        if not issues:
            st.success(f'已辨識 {len(found)} 題，通過基本形式檢查。仍須由教師核對答案、干擾選項與語言難度。')
        else:
            for issue in issues: st.warning(issue)
    st.divider()
    st.subheader('第四階段｜補充生詞、編排與匯出 Word')
    st.caption('閱讀前補充生詞是可選的。只收入教師確認需要解釋的詞，不自動列出所有疑似超綱詞。')
    approved_retained=st.session_state.get('approved_retained',[])
    if 'glossary_editor' not in st.session_state:
        st.session_state['glossary_editor']='\n'.join(f'{w}：' for w in approved_retained)
    glossary=st.text_area('學生版補充生詞（每行「詞語：簡短解釋」；可刪除不需要的詞）',key='glossary_editor',height=115)
    show_glossary=st.checkbox('將補充生詞印在閱讀文本之前',value=bool(approved_retained))
    doc_title=st.text_input('文件標題',value=f"{book}{lesson['課']} 閱讀測驗")
    st.caption('學生版：標題、補充生詞（選用）、閱讀文本、測驗題。教師版在最後另附答案頁。頁面邊界與字級已設定，可於 Word 調整 A4 紙張與列印。')
    if not quiz_student.strip():
        st.info('尚未貼入測驗題。目前可先下載「只有閱讀文本」的版本；完成測驗後再輸出完整講義。')
    if issues and quiz_student.strip():
        st.warning('題目形式檢測仍有提醒，建議先修正再印發。')
    export_glossary=glossary if show_glossary else ''
    student_doc=write_docx(final,quiz_student,'',doc_title,export_glossary,False)
    teacher_doc=write_docx(final,quiz_student,output_answer,doc_title,export_glossary,True)
    a,b=st.columns(2)
    with a:
        st.download_button('下載學生版 Word（無答案）',data=student_doc,file_name='ReadingParallel_學生版.docx',mime='application/vnd.openxmlformats-officedocument.wordprocessingml.document')
    with b:
        st.download_button('下載教師版 Word（附答案）',data=teacher_doc,file_name='ReadingParallel_教師版.docx',mime='application/vnd.openxmlformats-officedocument.wordprocessingml.document')

st.caption('版本說明：疑似超綱檢測為繁體大字典（可用時）＋ Jieba 斷詞＋教材詞庫優先匹配＋截至本課生詞表比對，不等於完整已學詞庫。人名、功能詞、句法、自然度及複現適切性需要教師審查；目前不自動呼叫付費 API。')
