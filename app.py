# -*- coding: utf-8 -*-
"""ReadingParallel Python v1.7: independent teacher-in-the-loop LLM prompt + audit app."""
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
# 提高輸入框、選單、操作階段的辨識度；不改變原有 Streamlit 操作方式。
st.markdown('''
<style>
/* 多行輸入框與一般輸入框：加深邊線以利教師辨識 */
div[data-testid="stTextArea"] textarea,
div[data-testid="stTextInput"] input {
    border: 2px solid #64748b !important;
    border-radius: 7px !important;
    background-color: #ffffff !important;
    color: #172033 !important;
}
div[data-testid="stTextArea"] textarea:focus,
div[data-testid="stTextInput"] input:focus {
    border: 2px solid #175cd3 !important;
    box-shadow: 0 0 0 2px rgba(23,92,211,.15) !important;
}
/* 深色主題下維持可讀性，並標示輸入框 */
@media (prefers-color-scheme: dark) {
  div[data-testid="stTextArea"] textarea,
  div[data-testid="stTextInput"] input { background-color: #202b3d !important; color: #f8fafc !important; }
}
/* 四階段標題與原本的細分隔線建立清楚的工作流程 */
div[data-testid="stMainBlockContainer"] h3 {
    border-left: 5px solid #2563eb;
    background: rgba(76,129,205,.09);
    padding: 9px 12px;
    margin-top: 14px;
    border-radius: 3px;
}
div[data-testid="stSidebar"] { border-right: 2px solid #94a3b8; }
div[data-testid="stDownloadButton"] button { border: 2px solid #2563eb; font-weight: 650; }
</style>
''', unsafe_allow_html=True)
st.title('高複現華語閱讀文本工作室 · Python v1.7')
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


def names_from_dialogue(text):
    """只擷取明確的對話說話者標籤；不猜測內文中的姓名。"""
    return uniq(m.group(1) for line in text.splitlines()
                if (m := re.match(r'^\s*([^：:，。！？\s]{1,8})[：:]', line))
                and not re.match(r'^(?:文本|老師|教師備註)\d*$', m.group(1)))


def known_names(upto):
    return uniq(name for d in DATA[:upto+1] for part in PARTS
                for name in names_from_dialogue(d.get(part,'')) + explicit_names_from_source(d.get(part,'')))


def explicit_names_from_source(source):
    """可確定的自我介紹句型；其他人名請教師確認，不自行臆測。"""
    candidates=[]
    for pat in (r'我是([\u3400-\u9fff]{2,4})(?=[。！？，])',
                r'我叫([\u3400-\u9fff]{2,4})(?=[。！？，])',
                r'這是([\u3400-\u9fff]{2,4})(?=先生|小姐|。|，)',):
        candidates.extend(re.findall(pat,source))
    return uniq(candidates)


@st.cache_data(show_spinner=False)
def lesson_segmented_corpus(upto: int, dict_version: str):
    """重建至目標課為止的全課文分詞集合；不同於本課生詞表。

    現有 corpus.json 未保存原始教材斷詞欄位，這是依課文及指定 Jieba 字典
    的可重現重建，不應冒稱原始人工校訂分詞資料。
    """
    from_text=set()
    source_sentences=[]
    for d in DATA[:upto+1]:
        for part in PARTS:
            raw=d.get(part,'')
            for line in raw.splitlines():
                # 除去說話者標記，保留正文、避免姓名干擾詞庫
                body=re.sub(r'^\s*[^：:\n]{1,12}[：:]\s*', '',line)
                for sentence in re.split(r'[，。！？；：、,.!?;：\s]+',body):
                    sentence=sentence.strip()
                    if not sentence: continue
                    source_sentences.append(sentence)
                    from_text.update(w for w in TOKENIZER.lcut(sentence,HMM=False) if HAN_RE.search(w))
    return from_text, tuple(source_sentences)


def curriculum_evidence(upto):
    """截至目標課：全課文斷詞（主要已學集合）＋語法例句詞彙。"""
    version=f'{BIG_DICT}:{BIG_DICT.stat().st_mtime if BIG_DICT else 0}'
    from_text,source_sentences=lesson_segmented_corpus(upto,version)
    from_grammar=set()
    grammar_sentences=[]
    for d in DATA[:upto+1]:
        for part in PARTS:
            for g in d.get(part+'語法點',[]):
                example=g.split('：',1)[1].strip() if '：' in g else ''
                if example:
                    grammar_sentences.extend(x for x in re.split(r'[，。！？；：、,.!?;\s]+',example) if x)
                    from_grammar.update(w for w in TOKENIZER.lcut(example,HMM=False) if HAN_RE.search(w))
                    # 短語如「貴姓」可能被 Jieba 拆成單字；連續出現即應有教材證據。
                    if '貴姓' in example: from_grammar.add('貴姓')
    return from_text, from_grammar, source_sentences, tuple(grammar_sentences)


def speaker_warnings(text,approved_names):
    allowed=set(approved_names)
    return [n for n in names_from_dialogue(text) if n not in allowed]


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


def audit(text,known,target,original,allow,text_evidence=None,grammar_evidence=None,names=None,attested_phrases=None):
    text_evidence=set(text_evidence or [])
    grammar_evidence=set(grammar_evidence or [])
    names=set(names or [])
    attested_phrases=tuple(attested_phrases or ())
    # 先處理教材證據、語法例句及核准人名，再進入超綱檢測。
    baseline=set(known)|set(allow)|text_evidence|grammar_evidence|names
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
    evidence_counts=Counter()
    counts={w:len(re.findall(re.escape(w), normalized)) for w in target}
    known_set=baseline | set('的一了是不我你他她們嗎呢啊有在和也都很去來說要會')
    for w in tokens:
        if not HAN_RE.search(w):
            continue
        if w in names: evidence_counts['教師確認人名'] += 1
        elif w in known: evidence_counts['已學生詞'] += 1
        elif w in grammar_evidence: evidence_counts['語法例句'] += 1
        elif w in text_evidence or (len(w)>1 and any(w in phrase for phrase in attested_phrases)): evidence_counts['已學課文連續用詞'] += 1
        elif w in allow: evidence_counts['教師允許詞'] += 1
        elif w in known_set: evidence_counts['常見功能詞'] += 1
        else: unknown[w]+=1
    overlap,examples=ngram_overlap(original,text)
    old=normalize_text(original); new=normalize_text(text)
    sim=SequenceMatcher(None,old,new,autojunk=False).ratio() if old and new else 0
    return {'字數':count_han(text),'目標詞複現':counts,'疑似超綱':unknown,'疑似超綱詞次':sum(unknown.values()),
            '全文中文字詞次':sum(1 for w in tokens if HAN_RE.search(w)), '已學證據分類':evidence_counts,
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
    """A4 教材版：區塊留白、題目不拆頁、善用兩欄選項與穩定的中文行距。"""
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
    from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Cm(21), Cm(29.7)
    sec.top_margin, sec.bottom_margin = Cm(1.55), Cm(1.55)
    sec.left_margin, sec.right_margin = Cm(1.7), Cm(1.7)
    sec.header_distance, sec.footer_distance = Cm(.7), Cm(.7)
    body_width = 21 - 2 * 1.7

    normal = doc.styles['Normal']
    normal.font.name = 'Microsoft JhengHei'
    normal.font.size = Pt(11)
    normal.paragraph_format.line_spacing = 1.22
    normal.paragraph_format.space_before = Pt(0)
    normal.paragraph_format.space_after = Pt(0)

    def style_run(run, size=11, bold=False):
        run.font.name = 'Microsoft JhengHei'
        run.font.size = Pt(size)
        run.bold = bold
        run._element.rPr.rFonts.set(qn('w:eastAsia'), 'Microsoft JhengHei')
        return run

    def line(value, *, after=0, before=0, keep_next=False, bold=False):
        p = doc.add_paragraph(style='Normal')
        f=p.paragraph_format
        f.space_before, f.space_after = Pt(before), Pt(after)
        f.keep_together=True
        f.keep_with_next=keep_next
        style_run(p.add_run(value),bold=bold)
        return p

    def heading(value):
        p = line(value, before=11, after=5, keep_next=True, bold=True)
        for r in p.runs: r.font.size=Pt(11.5)
        return p

    def no_table_borders(table):
        tblPr=table._tbl.tblPr
        borders=OxmlElement('w:tblBorders')
        for side in ('top','left','bottom','right','insideH','insideV'):
            x=OxmlElement('w:'+side)
            x.set(qn('w:val'),'nil')
            borders.append(x)
        tblPr.append(borders)

    def choice_row(options, *, keep_next=False):
        # Word table two equal-width columns, visually uses page width.
        tb=doc.add_table(rows=1, cols=2)
        tb.autofit=False
        tb.alignment=WD_TABLE_ALIGNMENT.CENTER
        no_table_borders(tb)
        for j, option in enumerate(options):
            cell=tb.cell(0,j)
            cell.width=Cm(body_width/2)
            cell.vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.CENTER
            cp=cell.paragraphs[0]
            cp.paragraph_format.space_before=Pt(0)
            cp.paragraph_format.space_after=Pt(0)
            cp.paragraph_format.line_spacing=1.2
            cp.paragraph_format.keep_together=True
            cp.paragraph_format.keep_with_next=keep_next
            style_run(cp.add_run(option))
        return tb

    head=doc.add_paragraph(style='Normal')
    head.alignment=WD_ALIGN_PARAGRAPH.CENTER
    head.paragraph_format.space_after=Pt(9)
    style_run(head.add_run(title.strip() or '閱讀測驗'),size=15,bold=True)

    items=glossary_lines(glossary_text)
    if items:
        heading('閱讀前補充生詞')
        # 短生詞兩欄，較長解釋自然換行；不以全形空格硬撐欄距。
        for i in range(0,len(items),2):
            vals=[f'{w}：{m}' if m else w for w,m in items[i:i+2]]
            vals += ['']*(2-len(vals))
            choice_row(vals)

    heading('閱讀文本')
    content=[x.strip() for x in text.strip().splitlines() if x.strip()]
    for j, value in enumerate(content):
        line(value, after=1.2)

    question_re=re.compile(r'^(?:\d{1,2}[.．、)]|[（(]\d{1,2}[)）]|第[一二三四五六七八九十\d]+題)\s*')
    option_re=re.compile(r'^([A-D])[.．、)）\s]+',re.I)
    if quiz.strip():
        heading('閱讀測驗')
        qlines=[x.strip() for x in quiz.strip().splitlines() if x.strip()]
        # Parse question blocks; an explicit question number starts new block.
        blocks=[]
        current=[]
        for val in qlines:
            if question_re.match(val) and current:
                blocks.append(current)
                current=[]
            current.append(val)
        if current: blocks.append(current)
        for block_index,block in enumerate(blocks):
            if not block:continue
            q=block[0]
            if not question_re.match(q):
                # Instructions such as「請選出正確答案」 remain outside question blocks.
                line(q,after=4,keep_next=True)
                rest=block[1:]
                question_started=False
            else:
                # Question begins new visual block; the question and its answer choices stay together.
                line(q,before=(4 if block_index==0 else 10),after=3,keep_next=True)
                rest=block[1:]
                question_started=True
            opts=[]
            for item in rest:
                if option_re.match(item):
                    opts.append(item)
                else:
                    if opts:
                        for k in range(0,len(opts),2):
                            choice_row((opts[k:k+2]+[''])[:2],keep_next=(k+2<len(opts)))
                        opts=[]
                    line(item,after=2,keep_next=question_started)
            if opts:
                # Only pair short options. Long or full sentence options use all available width.
                if len(opts)==4 and all(len(x)<=24 for x in opts):
                    choice_row(opts[:2],keep_next=True)
                    choice_row(opts[2:],keep_next=False)
                else:
                    for j,opt in enumerate(opts):
                        line(opt,after=1,keep_next=j<len(opts)-1)

    if teacher_version and answer.strip():
        doc.add_page_break()
        heading('教師答案')
        for value in answer.strip().splitlines():
            if value.strip():line(value.strip(),after=3)

    out=io.BytesIO()
    doc.save(out)
    return out.getvalue()


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


def audit_quiz_language(quiz, known, allow, text_evidence=None, grammar_evidence=None, names=None, attested_phrases=None):
    question_stems, choices=extract_quiz_language(quiz)
    r1=audit(question_stems,known,[], '',allow,text_evidence,grammar_evidence,names,attested_phrases)
    r2=audit(choices,known,[], '',allow,text_evidence,grammar_evidence,names,attested_phrases)
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
    st.subheader('人物姓名（生成前先確認）')
    st.caption('可選截至本課所有已出現的名字（含前面各課），不限制當課人名；教師也可自行增加。')
    source_people=names_from_dialogue(lesson[reference])
    historical_people=known_names(idx)
    selected_names=st.multiselect('允許使用的教材說話者',historical_people,
                                  default=[n for n in source_people if n in historical_people],
                                  key=f'names_{idx}_{reference}')
    manual_names_raw=st.text_area('其他允許人名（每行一人，選填；也可填教材中的完整姓名）',
                                  value='\n'.join(explicit_names_from_source(lesson[reference])),height=75,
                                  key=f'extra_names_{idx}_{reference}')
    approved_names=uniq(selected_names+re.split(r'[,，、\n]+',manual_names_raw))
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
text_evidence, grammar_evidence, original_sentences, grammar_sentences=curriculum_evidence(idx)
attested_phrases=original_sentences+grammar_sentences
current_key=f'{idx}:{reference}:{form}:{n}'
if st.session_state.get('active_key')!=current_key:
    st.session_state['active_key']=current_key
    for k in ['draft','final_text','quiz_text','answer_text','approved','last_audit','audited_text','retained_words','glossary_editor','quiz_allowed_raw','audited_names']:
        st.session_state.pop(k,None)

with st.expander('查看教材來源與複現目標',expanded=False):
    st.write('**原課文：**');st.text(source)
    st.write('**當課目標生詞：**','、'.join(target) or '（原文未載）')
    st.write('**當課目標語法：**','；'.join(gtarget) or '（原文未載）')
    st.caption(f'截至本課：全課文重建斷詞 {len(text_evidence)} 項（主要已學比對集合）；生詞表 {len(known)} 項（複現目標來源）；語法例句 {len(grammar_evidence)} 項；整課字數約 {whole_len} 字。')
    st.info('目前 JSON 未保留原始人工校訂的全課斷詞清單；此處從完整課文使用本專案 Jieba 字典重建。若您有原始斷詞 JSON，可再替換。')

st.subheader('第一階段｜生成真正的新文本')
with st.expander('生成提示詞',expanded=True):
    prompt1=f'''你是臺灣對外華語教材編寫者。請生成{n}篇**新的**{form}，供「{book} {lesson['課']}」學習者複習。
你必須創作新的事件，不是重寫、複製、換句話說、延長或續寫原課文。
【原課文（僅供理解教學情境，禁止照搬）】\n{source}
【任務】
1. 新文本必須有明確不同於原課文的事件目的、重要資訊或結果；不能只更換人物姓名、地點或對話順序。
2. 只可使用下列教師已確認的人名／說話者：{'、'.join(approved_names) or '無（請使用無人名的文章）'}。不得新增姓名或自創說話者；如果需要代稱，可用已學的『我、你、他、她』。{('指定新情境：'+theme) if theme else '請自行提出有變化的新情境。'}
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
        report=audit(raw,known,target,source,allowed,text_evidence,grammar_evidence,approved_names,attested_phrases)
        report['未核准說話者']=speaker_warnings(raw,approved_names) if form=='對話' else []
        st.session_state['last_audit']=report
        st.session_state['audited_text']=raw
        st.session_state['audited_names']=tuple(sorted(approved_names))
        st.session_state['approved']=False
report=st.session_state.get('last_audit')
if report:
    c1,c2,c3,c4=st.columns(4)
    c1.metric('正文中文字數',report['字數'])
    c2.metric('目標詞至少出現一次',f"{sum(bool(v) for v in report['目標詞複現'].values())}/{len(target)}")
    c3.metric('教材未收錄詞次',report['疑似超綱詞次'])
    c4.metric('與原文相似度',f"{report['整體字元相似度']:.0%}")
    st.caption('先採截至本課的全課文斷詞與課文連續用詞，再對照生詞表、語法例句、歷來已學人名與教師允許詞。未找到的項目仍需核對。')
    st.write('**檢測依據統計：**', '；'.join(f'{k} {v} 詞次' for k,v in report['已學證據分類'].items()))
    if report['未核准說話者']:
        st.error('出現未經教師核准的說話者：'+'、'.join(report['未核准說話者'])+'。請先改名、調整允許名單，然後重新檢測。')
    if report['原文連續片段重合率']>.25 or report['整體字元相似度']>.7:
        st.error('與原課文的相似度偏高：請人工確認是否只是在照搬、少量改寫或延續原對話。')
    if not(target_len[0]<=report['字數']<=target_len[1]*n):
        st.info('篇幅提醒：請逐篇檢查是否符合每篇字數範圍；目前顯示總正文概估字數。')
    left,right=st.columns(2)
    with left:
        st.markdown('**目標詞複現次數**')
        st.dataframe([{'生詞':w,'次數':v} for w,v in report['目標詞複現'].items()],hide_index=True,use_container_width=True)
    with right:
        st.markdown('**教材詞庫未收錄詞（待核對）**')
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
    names_changed=st.session_state.get('audited_names') != tuple(sorted(approved_names))
    if st.session_state.get('audited_text') != st.session_state.get('draft') or names_changed:
        st.warning('文本或核准人名已在上次檢測後變更，請重新檢測。')
    if st.button('將目前檢測內容確認為定稿', disabled=(st.session_state.get('audited_text') != st.session_state.get('draft') or names_changed or bool(report['未核准說話者']))):
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
    quiz_language=audit_quiz_language(quiz_student,known,allowed+quiz_allowed,text_evidence,grammar_evidence,approved_names,attested_phrases)
    stems_bad=quiz_language['題幹與說明']['疑似超綱']
    choices_bad=quiz_language['選項']['疑似超綱']
    if quiz_student.strip():
        c_stem,c_choice=st.columns(2)
        with c_stem:
            st.metric('題幹／說明未收錄詞次',sum(stems_bad.values()))
            if stems_bad:
                st.dataframe([{'疑似超綱詞':w,'次數':c} for w,c in stems_bad.most_common()],hide_index=True,use_container_width=True)
            else:
                st.success('目前未標記題幹或說明的疑似超綱詞。')
        with c_choice:
            st.metric('選項未收錄詞次',sum(choices_bad.values()))
            if choices_bad:
                st.dataframe([{'疑似超綱詞':w,'次數':c} for w,c in choices_bad.most_common()],hide_index=True,use_container_width=True)
            else:
                st.success('目前未標記選項的疑似超綱詞。')
        st.caption('已先扣除生詞表、已學課文、語法例句與核准人名；剩餘為教材未收錄詞候選，仍須教師依語境核對。')
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
st.subheader('第四階段｜編輯與匯出 Word（隨時可用）')
st.caption('即使命題尚未完成，也可先匯出閱讀文本；完成命題後，將題目貼到下方即可匯出完整學生版與教師版。')
# 不能把尚未定稿的舊文本誤認為正式定稿；提供可編輯的匯出欄位。
if st.session_state.get('approved'):
    export_default=st.session_state.get('final_text','')
else:
    export_default=st.session_state.get('draft','')
if st.session_state.get('word_source')!=export_default and not st.session_state.get('word_customized'):
    st.session_state['word_body']=export_default
    st.session_state['word_source']=export_default
export_body=st.text_area('Word 閱讀正文（可直接編輯；未定稿時請自行確認內容）',key='word_body',height=230)
if st.session_state.get('word_source')!=export_body:
    st.session_state['word_customized']=True
quiz_for_word=st.session_state.get('quiz_text','')
quiz_for_word=re.split(r'教師答案\s*[:：]?\s*',quiz_for_word,maxsplit=1)[0]
if st.session_state.get('word_quiz_source')!=quiz_for_word and not st.session_state.get('word_quiz_customized'):
    st.session_state['word_quiz']=quiz_for_word
    st.session_state['word_quiz_source']=quiz_for_word
export_quiz=st.text_area('Word 測驗題目（可直接貼入或修改）',key='word_quiz',height=220)
if st.session_state.get('word_quiz_source')!=export_quiz:
    st.session_state['word_quiz_customized']=True
export_answers=st.text_area('教師答案（僅教師版收錄）',value=st.session_state.get('answer_editor',''),height=90,key='word_answers')
if not st.session_state.get('word_glossary_loaded'):
    st.session_state['word_glossary']='\n'.join(f'{w}：' for w in st.session_state.get('approved_retained',[]))
    st.session_state['word_glossary_loaded']=True
export_glossary=st.text_area('閱讀前補充生詞（選填，每行：詞語：解釋）',key='word_glossary',height=100)
show_glossary=st.checkbox('在學生版與教師版列印補充生詞',value=bool(export_glossary.strip()))
doc_title=st.text_input('文件標題',value=f"{book}{lesson['課']} 閱讀測驗",key='word_title')
if not export_body.strip():
    st.warning('請先貼入閱讀正文才可下載 Word。')
else:
    if not export_quiz.strip():
        st.info('目前只有閱讀正文，下載的是閱讀文本；命題後可再下載含測驗題的完整 Word。')
    if not st.session_state.get('approved'):
        st.caption('目前尚未完成教師定稿，下載前請檢查文本內容。')
    student_doc=write_docx(export_body,export_quiz,'',doc_title,export_glossary if show_glossary else '',False)
    teacher_doc=write_docx(export_body,export_quiz,export_answers,doc_title,export_glossary if show_glossary else '',True)
    a,b=st.columns(2)
    with a:
        st.download_button('下載學生版 Word（無答案）',data=student_doc,file_name='ReadingParallel_學生版.docx',mime='application/vnd.openxmlformats-officedocument.wordprocessingml.document',key='word_student_download')
    with b:
        st.download_button('下載教師版 Word（附答案）',data=teacher_doc,file_name='ReadingParallel_教師版.docx',mime='application/vnd.openxmlformats-officedocument.wordprocessingml.document',key='word_teacher_download')

st.caption('v1.7：先以截至目標課的全課文重建斷詞集合核對已學詞；生詞表另作複現目標；人名累積前面各課。第四階段 Word 可獨立使用。無需 LLM API。')
