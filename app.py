# -*- coding: utf-8 -*-
"""ReadingParallel Python v1.2: independent teacher-in-the-loop LLM prompt + audit app."""
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
st.title('高複現華語閱讀文本工作室 · Python v1.2')
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


def write_docx(text,quiz,answer,title):
    doc=Document(); sec=doc.sections[0]; sec.top_margin=sec.bottom_margin=Cm(2.2)
    normal=doc.styles['Normal'];normal.font.name='Microsoft JhengHei';normal.font.size=Pt(13)
    normal.paragraph_format.line_spacing=1.5
    doc.add_heading(title,0)
    doc.add_heading('閱讀文本',level=1)
    for t in text.strip().split('\n'):
        doc.add_paragraph(t)
    if quiz.strip():
        doc.add_heading('閱讀測驗',level=1)
        for t in quiz.strip().split('\n'):
            doc.add_paragraph(t)
    if answer.strip():
        doc.add_page_break();doc.add_heading('教師答案',level=1)
        for t in answer.strip().split('\n'):
            doc.add_paragraph(t)
    buf=io.BytesIO();doc.save(buf);return buf.getvalue()


def split_items(text):
    # Accept common LLM numbered questions: 1. / 1、 / 第一題 / （1）
    lines=text.splitlines()
    matches=[]
    for line in lines:
        if re.match(r'^\s*(?:\d{1,2}[.．、)]|[（(]\d{1,2}[)）]|第[一二三四五六七八九十\d]+題)',line):
            matches.append(line.strip())
    return matches


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

source=lesson[reference]; target=words(lesson,[reference]); gtarget=grammar(lesson,[reference])
known=get_lexicon(idx)
current_key=f'{idx}:{reference}:{form}:{n}'
if st.session_state.get('active_key')!=current_key:
    st.session_state['active_key']=current_key
    for k in ['draft','final_text','quiz_text','answer_text','approved','last_audit']:
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
    with st.expander('生成修訂提示詞（選用）'):
        notes=st.text_area('教師修訂意見',placeholder='例如：保留人物，但改變事件；增加當課未使用詞；調整自然度。')
        suspects='、'.join(f'{w}({c})' for w,c in report['疑似超綱'].most_common(45)) or '未檢出'
        lack='、'.join(w for w,c in report['目標詞複現'].items() if not c) or '無'
        prompt2=f'''請只修訂下列已生成的華語閱讀文本，不要寫新篇章，不要續寫原課文。
【教師意見】{notes or '優先處理疑似超綱並維持語句自然。'}
【原教材來源，僅供避免照搬】\n{source}
【本次待修訂文本】\n{st.session_state['draft']}
【Python 檢測（疑似而非確定錯誤）】\n疑似超綱：{suspects}\n未出現目標生詞：{lack}\n與原文相似度：{report['整體字元相似度']:.0%}
【要求】逐項判斷是否真的超綱（含人名及語境），必要時改為截至本課學過的詞；不能只把超綱詞拆成單字，不能犧牲語意正確或自然度。維持原先篇數、各篇完整事件、{target_len[0]}至{target_len[1]}字左右。若原課文重合過高，請改變事件目的、資訊或結果。只輸出修訂後的完整文本，禁止輸出詞彙來源、對話分析、解釋或修訂報告。'''
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
        st.success('已鎖定此份文本作為命題依據。')

st.divider()
st.subheader('第三階段｜只依定稿文本命題')
if not st.session_state.get('approved'):
    st.info('請先在第二階段完成檢測，並按「確認為定稿」。')
else:
    final=st.session_state['final_text']
    a,b,c=st.columns(3)
    with a: qtype=st.selectbox('題型',['單選題','是非題','簡答題','事件排序','混合題型'])
    with b: qty=st.selectbox('題數',[3,4,5,6],index=0)
    with c: qscope=st.selectbox('命題重點',['訊息擷取與理解','推論與整合','混合'])
    prompt3=f'''你是臺灣華語閱讀測驗命題教師。**本次任務只寫題目，不要創作、改寫或續寫閱讀文本。**
【唯一命題依據：教師已審定閱讀文本】\n{final}
【命題指令】
- 題型：{qtype}。共 {qty} 題。重點：{qscope}。
- 每題須能根據上述閱讀文本回答。不得詢問教材出版社、詞彙出自第幾冊第幾課或語法術語。
- 題幹及選項應盡量使用文本既有詞彙與初級學習者已學用語，不加不必要的背景知識。
- 單選題每題必須有 A、B、C、D 四個選項，且只有一個正確答案。
- 是非題每題是可以判定對錯的陳述句；簡答題須有可驗證的答案；事件排序題須有明確事件序列。
【禁止】禁止輸出任何新對話、新閱讀文本、詞語定義、詞彙來源清單、課別分析、教師備註、命題原理，禁止出現「對話二」或「文本二」作為新創作。
【輸出格式，必須嚴格遵守】
閱讀測驗
1. [第一題完整題幹]
{('A. [選項]；B. [選項]；C. [選項]；D. [選項]' if qtype in ['單選題','混合題型'] else '[按指定題型提供學生作答內容]')}
2. [第二題完整題幹]
依此編寫至第 {qty} 題。
教師答案
1. [答案]
依序列出第 {qty} 題答案。
請直接給真正可作答的題目，不能只填空白模板、不能解釋規則。'''
    format_prompt(prompt3)
    quiz_raw=st.text_area('貼回 LLM 命題結果（可以包含「教師答案」段落）',height=220,key='quiz_text')
    if st.button('檢查是否真的產生題目'):
        main=re.split(r'教師答案\s*[:：]?\s*',quiz_raw,maxsplit=1)[0]
        found=split_items(main)
        if len(found)<qty:
            st.error(f'偵測到 {len(found)} 道題目，少於設定的 {qty} 題。請要求 LLM 重新依格式命題，或人工修改。')
        else:
            st.success(f'偵測到至少 {len(found)} 個題號，請再核對選項及答案內容。')
        if re.search(r'(?:【?對話[一二三123]】?|詞彙來源|第[一二三123]冊第)',main):
            st.warning('命題內容可能混入對話續寫或教材詞彙來源，建議刪除並重新命題。')
        if qtype=='單選題' and sum(bool(re.search(r'\b[A-D][.．、]',line)) for line in main.splitlines())<qty:
            st.warning('部分單選題可能缺少四個選項，請人工核對。')
    answer=st.text_area('教師答案（可自行貼入或編輯；匯出時另起一頁）',height=100,key='answer_text')
    doc_title=st.text_input('Word 文件標題',value=f"{book}{lesson['課']} 閱讀測驗")
    quiz_student, *quiz_answer = re.split(r'教師答案\s*[:：]?\s*',quiz_raw,maxsplit=1)
    output_answer = answer.strip() or (quiz_answer[0].strip() if quiz_answer else '')
    st.download_button('下載 Word 閱讀測驗（.docx）',data=write_docx(final,quiz_student,output_answer,doc_title),file_name='ReadingParallel_閱讀測驗.docx',mime='application/vnd.openxmlformats-officedocument.wordprocessingml.document')

st.caption('版本說明：疑似超綱檢測為繁體大字典（可用時）＋ Jieba 斷詞＋教材詞庫優先匹配＋截至本課生詞表比對，不等於完整已學詞庫。人名、功能詞、句法、自然度及複現適切性需要教師審查；目前不自動呼叫付費 API。')
