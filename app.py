# -*- coding: utf-8 -*-
"""ReadingParallel Python v2.2: LLM self-review, verified JSON, quiz glossary safety."""
from __future__ import annotations
import io
import json
import re
import sys
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

import jieba
import streamlit as st
from docx import Document
from docx.shared import Pt, Cm

HERE = Path(__file__).resolve().parent
# AppTest scripts may start with tests/ as sys.path[0]; sibling imports still need HERE.
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from target_settings import combine_targets, minimum_matrix, configuration_signature, invalidate_audit
from vocabulary_audit import audit_per_text
from revision_prompts import revision_plan, revision_prompt
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
st.title('高複現華語閱讀文本工作室 · Python v2.2')
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



def curriculum_word_index(baseline):
    """Shared learned/allowed-word precedence; targets are never added here."""
    by_first = {}
    for word in sorted((w for w in baseline if len(w)>1 and CJK_WORD.fullmatch(w)),key=len,reverse=True):
        by_first.setdefault(word[0], []).append(word)
    return by_first


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
    by_first = curriculum_word_index(baseline)
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



def per_text_target_audit(text, config, known, allow, text_evidence, grammar_evidence, names):
    baseline = set(known) | set(allow) | set(text_evidence) | set(grammar_evidence) | set(names)
    index = curriculum_word_index(baseline)

    def token_views(fragment):
        return _curriculum_segment(fragment, index), TOKENIZER.lcut(fragment, HMM=False)

    return audit_per_text(text, config, token_views, cleanbody, CJK_WORD)


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
            # Multilingual definitions are wide: give them full text width.
            if any('；' in m for _,m in items):
                break
            vals=[f'{w}：{m}' if m else w for w,m in items[i:i+2]]
            vals += ['']*(2-len(vals))
            choice_row(vals)
        if any('；' in m for _,m in items):
            for w,m in items:
                line(f'{w}：{m}' if m else w,after=3)

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


def audit_quiz_by_item(raw, known, allow, text_evidence=None, grammar_evidence=None, names=None, attested_phrases=None):
    """逐題檢查；題目前的作答說明也納入，避免只報總數。"""
    header=re.compile(r'^\s*(?:\d{1,2}[.．、)）]|[（(]\d{1,2}[)）]|第[一二三四五六七八九十\d]+題)\s*')
    lines=raw.splitlines()
    groups=[]; current=[]; label='作答說明'
    for line in lines:
        m=header.match(line)
        if m:
            if current and any(x.strip() for x in current): groups.append((label,'\n'.join(current)))
            label=f'第 {len([g for g in groups if g[0].startswith("第 ")])+1} 題'
            current=[line]
        else:
            current.append(line)
    if current and any(x.strip() for x in current): groups.append((label,'\n'.join(current)))
    output=[]
    for label,body in groups:
        checked=audit_quiz_language(body,known,allow,text_evidence,grammar_evidence,names,attested_phrases)
        stems=checked['題幹與說明']['疑似超綱']; choices=checked['選項']['疑似超綱']
        output.append({'區塊':label,'題幹／說明待核對':'、'.join(stems) or '—',
                       '選項待核對':'、'.join(choices) or '—',
                       '待核對詞次':sum(stems.values())+sum(choices.values())})
    return output


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


# v2.0：第三階段的結構化輸出由第四階段一次載入，不再重新逐欄貼上。
def parse_learning_json(raw):
    """Accept a single JSON object, optionally fenced by Markdown; reject unknown structures."""
    s=raw.strip().lstrip('\ufeff')
    s=re.sub(r'^```(?:json)?\s*', '', s, flags=re.I)
    s=re.sub(r'\s*```$', '', s)
    try:
        data=json.loads(s)
    except json.JSONDecodeError as exc:
        raise ValueError(f'JSON 語法錯誤：第 {exc.lineno} 行，第 {exc.colno} 欄：{exc.msg}') from exc
    if not isinstance(data,dict):
        raise ValueError('最外層須為 JSON 物件。')
    texts=data.get('reading_texts')
    if not isinstance(texts,list) or not texts or not all(isinstance(x,dict) and isinstance(x.get('content'),str) and x['content'].strip() for x in texts):
        raise ValueError('reading_texts 必須是至少一篇、且每篇有 content 的陣列。')
    for i,text in enumerate(texts,1):
        if not isinstance(text.get('title',''),str):
            raise ValueError(f'第 {i} 篇閱讀文本 title 須為字串。')
    vocab=data.get('supplementary_vocabulary',[])
    if not isinstance(vocab,list) or not all(
        isinstance(x,dict) and isinstance(x.get('word'),str) and (
            isinstance(x.get('explanation'),str) or (
                isinstance(x.get('explanation'),dict) and
                all(isinstance(k,str) and isinstance(v,str) for k,v in x['explanation'].items())
            )
        ) for x in vocab):
        raise ValueError('supplementary_vocabulary 需有 word 與 explanation；explanation 可為文字或語言對照物件。')
    for vi,entry in enumerate(vocab,1):
        if 'source' in entry and (not isinstance(entry['source'],list) or
            any(x not in ('reading','question') for x in entry['source'])):
            raise ValueError(f'第 {vi} 個補充詞的 source 必須是 reading/question 陣列。')
    warnings=data.get('validation_warnings',[])
    if not isinstance(warnings,list) or not all(isinstance(w,str) for w in warnings):
        raise ValueError('validation_warnings 必須是文字陣列。')
    questions=data.get('questions',[])
    if not isinstance(questions,list):
        raise ValueError('questions 必須是陣列。')
    for i,q in enumerate(questions,1):
        if not isinstance(q,dict) or not isinstance(q.get('stem'),str) or not q['stem'].strip():
            raise ValueError(f'第 {i} 題缺少 stem（題幹）。')
        if not isinstance(q.get('type','single_choice'),str):
            raise ValueError(f'第 {i} 題 type 須為字串。')
        if not isinstance(q.get('options',[]),list):
            raise ValueError(f'第 {i} 題 options 須為陣列。')
        if not isinstance(q.get('answer',''),str):
            raise ValueError(f'第 {i} 題 answer 須為文字。')
    if not isinstance(data.get('instructions',''),str):
        raise ValueError('instructions 須為字串。')
    if not isinstance(data.get('title',''),str):
        raise ValueError('title 須為字串。')
    return data


def questions_to_text(items):
    lines=[]
    for i,q in enumerate(items,1):
        lines.append(f"{i}. {q['stem'].strip()}")
        for j,opt in enumerate(q.get('options',[])):
            label=chr(65+j) if j<26 else str(j+1)
            option=str(opt).strip()
            # A. B. 允許 LLM 在 option 內附上前綴，避免雙重選項標記。
            option=re.sub(r'^[A-Z][.．、)）]\s*','',option)
            lines.append(f'{label}. {option}')
    return '\n'.join(lines)


def json_to_editors(data):
    texts=data['reading_texts']
    body='\n\n'.join((f"{t.get('title','').strip()}\n" if t.get('title','').strip() else '')+t['content'].strip() for t in texts)
    def meaning_text(explanation):
        if isinstance(explanation,dict):
            return '；'.join(f'{language}：{meaning.strip()}' for language,meaning in explanation.items() if meaning.strip())
        return explanation.strip()
    vocab='\n'.join(f"{v['word'].strip()}：{meaning_text(v['explanation'])}" for v in data.get('supplementary_vocabulary',[]) if v['word'].strip())
    answers='\n'.join(f"{i}. {q.get('answer','').strip()}" for i,q in enumerate(data['questions'],1))
    return {'v2_title':data.get('title','閱讀測驗').strip() or '閱讀測驗',
            'v2_body':body, 'v2_vocab':vocab,
            'v2_instructions':data.get('instructions','').strip(),
            'v2_questions':questions_to_text(data['questions']), 'v2_answers':answers}


def serialize_quiz(instructions,questions):
    return '\n'.join(x for x in [instructions.strip(),questions.strip()] if x)



def normalized_glossary(vocab, selected_languages):
    """Collapse repeated glossary words while keeping all explanations and sources."""
    merged={}
    for entry in vocab:
        word=entry['word'].strip()
        if not word: continue
        record=merged.setdefault(word,{'word':word,'source':set(),'explanation':{}})
        record['source'].update(entry.get('source',[]))
        explain=entry.get('explanation',{})
        if isinstance(explain,dict):
            for lang,meaning in explain.items():
                if meaning.strip():record['explanation'][lang]=meaning.strip()
        elif explain.strip():
            record['explanation'].setdefault(selected_languages[0] if selected_languages else '繁體中文（臺灣）',explain.strip())
    return [{'word':v['word'],'source':sorted(v['source']), 'explanation':v['explanation']} for v in merged.values()]


def inspect_structured_quiz(data, expected_type=None, expected_qty=None):
    """Deterministic quiz structure checks; does not claim to validate meaning."""
    issues=[]
    questions=data.get('questions',[])
    if expected_qty is not None and len(questions)!=expected_qty:
        issues.append(f'題數不符：設定 {expected_qty} 題，實際 {len(questions)} 題。')
    options_allowed={'single_choice','true_false','short_answer','ordering'}
    answers=set()
    for i,q in enumerate(questions,1):
        tp=q.get('type','single_choice')
        opts=q.get('options',[])
        answer=q.get('answer','').strip()
        if tp not in options_allowed:
            issues.append(f'第 {i} 題未知題型：{tp}。')
        if expected_type=='單選題' and tp!='single_choice':
            issues.append(f'第 {i} 題不是指定的單選題。')
        if expected_type=='是非題' and tp!='true_false':
            issues.append(f'第 {i} 題不是指定的是非題。')
        if expected_type=='簡答題' and tp!='short_answer':
            issues.append(f'第 {i} 題不是指定的簡答題。')
        if expected_type=='事件排序' and tp!='ordering':
            issues.append(f'第 {i} 題不是指定的事件排序題。')
        if tp=='single_choice':
            if len(opts)!=4 or any(not isinstance(o,str) or not o.strip() for o in opts):
                issues.append(f'第 {i} 題須有四個非空白選項。')
            if answer.upper().strip(' .．、)）') not in ('A','B','C','D'):
                issues.append(f'第 {i} 題答案須為 A、B、C 或 D。')
            if len([o for o in opts if isinstance(o,str) and o.strip()])!=len(set(str(o).strip() for o in opts)):
                issues.append(f'第 {i} 題出現重複選項。')
        elif tp=='true_false' and answer not in ('對','錯','是','否','正確','不正確','True','False','true','false','O','X','○','×'):
            issues.append(f'第 {i} 題是非答案不明確：{answer or "空白"}。')
        elif not answer:
            issues.append(f'第 {i} 題缺少教師答案。')
        if q['stem'].strip() in answers:
            issues.append(f'第 {i} 題與其他題目題幹重複。')
        answers.add(q['stem'].strip())
    return issues


def quiz_vocab_review(instructions,questions,glossary_words,known,allowed,text_evidence,grammar_evidence,approved_names,attested_phrases):
    """Separate genuinely unaccounted words from supplied supplementary terms."""
    glossary_words={w.strip() for w in glossary_words if w.strip()}
    base_allow=set(allowed)
    rows=audit_quiz_by_item(serialize_quiz(instructions,questions),known,base_allow,
                            text_evidence,grammar_evidence,approved_names,attested_phrases)
    covered_rows=audit_quiz_by_item(serialize_quiz(instructions,questions),known,base_allow|glossary_words,
                                    text_evidence,grammar_evidence,approved_names,attested_phrases)
    for row,covered in zip(rows,covered_rows):
        initial=set((row['題幹／說明待核對']+'、'+row['選項待核對']).replace('—','').split('、'))-set([''])
        rest=set((covered['題幹／說明待核對']+'、'+covered['選項待核對']).replace('—','').split('、'))-set([''])
        row['已由補充生詞涵蓋']='、'.join(sorted(initial-rest)) or '—'
        row['尚未解決']='、'.join(sorted(rest)) or '—'
    return rows


def missing_glossary_explanations(vocab,selected_languages):
    warnings=[]
    for item in normalized_glossary(vocab,selected_languages):
        absent=[language for language in selected_languages if not item['explanation'].get(language,'').strip()]
        if absent: warnings.append(f"補充詞「{item['word']}」缺少解釋：{'、'.join(absent)}")
    return warnings


def editable_quiz_checks(instructions,questions,answers,requested_qty,requested_type):
    """Plain-text post-edit checks without requiring teacher to press a separate button."""
    issues=[]
    heads=split_items(questions)
    if questions.strip() and len(heads)!=requested_qty:
        issues.append(f'目前題目數 {len(heads)}，設定為 {requested_qty} 題。')
    if requested_type=='單選題' and questions.strip():
        for letter in 'ABCD':
            count=len(re.findall(r'(?m)^\s*'+letter+r'[.．、)）]\s*\S+',questions))
            if count<len(heads):issues.append(f'單選題選項 {letter} 只有 {count}/{len(heads)} 項。')
    actual_answers=[line for line in answers.splitlines() if re.match(r'^\s*\d+[.．、)）]\s*\S+',line)]
    if questions.strip() and len(actual_answers)!=len(heads):
        issues.append(f'教師答案 {len(actual_answers)} 項，題目 {len(heads)} 題。')
    return issues

def remember_draft_lesson(lesson_id):
    """Track the context of teacher edits without changing their draft."""
    st.session_state['draft_lesson_id'] = lesson_id
    invalidate_audit(st.session_state)


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

    st.subheader('目標複現詞與最低次數')
    candidates = words(lesson)
    target_store = st.session_state.setdefault('target_settings_by_lesson', {})
    saved_targets = target_store.setdefault(idx, {
        'selected': words(lesson, [reference]), 'manual': '', 'uniform': 1, 'minimums': {},
    })
    prefix = f'tv_{idx}'
    st.session_state.setdefault(prefix + '_selected', saved_targets['selected'])
    st.session_state.setdefault(prefix + '_manual', saved_targets['manual'])
    st.session_state.setdefault(prefix + '_uniform', saved_targets['uniform'])
    selected_targets = st.multiselect(
        '勾選本次目標詞（當課所有文本生詞）', candidates, key=prefix + '_selected',
        help='初次暫沿用參考課文生詞預選，可自由取消；切換同課來源不限制候選或重設選詞。',
    )
    manual_targets_raw = st.text_area(
        '手動增加目標詞（不自動視為已學或允許詞）', key=prefix + '_manual', height=75,
        help='可填以前的難詞或指定詞，以換行、逗號、分號或空白分隔；與引導詞、一般允許詞分開。',
    )
    target = combine_targets(selected_targets, manual_targets_raw)
    uniform_minimum = st.number_input(
        '批次最低次數（按套用才覆寫目前各格）', min_value=1, step=1, key=prefix + '_uniform',
    )
    st.caption('本批先支援至少1次的整數；新詞與新篇預設1次。批次套用會覆寫目前顯示的個別值，可再調整。')
    apply_uniform = st.button('套用至目前所有詞與篇章（覆寫個別值）', key=prefix + '_apply',
                              disabled=not target)
    target_minimums = minimum_matrix(target, n, saved_targets['minimums'])
    if apply_uniform:
        for word in target:
            for piece in range(1, n + 1):
                st.session_state[f'{prefix}_min_{word}_{piece}'] = int(uniform_minimum)
    with st.expander('逐詞逐篇最低次數', expanded=False):
        if not target:
            st.info('目前沒有選定目標詞；可勾選或手動新增。')
        for word in target:
            columns = st.columns(n)
            for piece, column in enumerate(columns, 1):
                cell_key = f'{prefix}_min_{word}_{piece}'
                st.session_state.setdefault(cell_key, target_minimums[word][piece])
                with column:
                    value = st.number_input(f'{word}｜第{piece}篇最低次數', min_value=1,
                                            step=1, key=cell_key)
                target_minimums[word][piece] = int(value)
                saved_targets['minimums'].setdefault(word, {})[piece] = int(value)
    saved_targets.update(selected=list(selected_targets), manual=manual_targets_raw,
                         uniform=int(uniform_minimum))
    st.session_state['target_settings_by_lesson'] = target_store
    target_config = {
        'lesson_id': idx, 'targets': target, 'selected': list(selected_targets),
        'manual_targets': combine_targets([], manual_targets_raw),
        'minimums': target_minimums, 'text_count': n, 'batch_value': int(uniform_minimum),
    }
    st.session_state['target_config'] = target_config

    whole='\n'.join(lesson.get(p,'') for p in PARTS)
    whole_len=count_han(whole)
    length=st.selectbox('每篇字數',['依整課課文總長度','100–150','200–300','400–500'])
    target_len={'依整課課文總長度':(max(30,round(whole_len*.8)),max(40,round(whole_len*1.2))), '100–150':(100,150),'200–300':(200,300),'400–500':(400,500)}[length]
    cover=st.selectbox('舊版覆蓋偏好（保留參考）',['自然度優先','每篇至少60%','每篇至少80%'],key='legacy_cover')
    st.caption('主要複現要求是逐詞逐篇最低次數；舊版百分比僅保留參考，不取代最低要求，也不代表已完成正式逐篇檢測。')
    theme=st.text_input('新情境／主題（選填）', placeholder='例如：從機場接人改為在校園第一次見面')
    guiding_raw=st.text_area(
        '補充引導詞（選填，建議 2～3 個）',
        height=76,
        placeholder='例如：機場、朋友、第一次',
        help='教師指定且允許使用的詞彙。供 LLM 發想新的事件與情境，僅作引導時不列入複現目標；若需計次請另選為目標。引導詞沿用教師允許集合。',
    )
    guiding_words=uniq(re.split(r'[,，、\n；;\s]+',guiding_raw))
    if guiding_words:
        st.caption('本次核准引導詞：'+'、'.join(guiding_words)+'。可自然出現在新文本中，並列入詞彙檢測的允許集合。')
        if len(guiding_words)>3:
            st.info('建議 2～3 個引導詞；目前仍全部視為教師允許詞。')
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
    # 引導詞是教師核准的詞，和一般允許詞一同作為檢測白名單；但不算本課複現目標。
    allowed=uniq(re.split(r'[,，、\n]+',allowed_raw)+guiding_words)
    st.divider()
    st.subheader('閱讀測驗預先設定')
    quiz_basis=st.selectbox('命題依據',['優先針對新舊文本變化','依閱讀理解目標','兩者混合'],index=0)
    quiz_type=st.selectbox('題型',['單選題','是非題','簡答題','事件排序','混合題型'])
    quiz_qty=st.selectbox('題數',[3,4,5,6],index=0)
    quiz_focus=st.selectbox('閱讀理解重點',['訊息擷取與理解','推論與整合','混合'])
    glossary_languages=st.multiselect(
        '補充生詞解釋語言（可多選）',
        ['繁體中文（臺灣）','English（英語）','Tiếng Việt（越南語）','Bahasa Indonesia（印尼語）','日本語（日語）','ภาษาไทย（泰語）','한국어（韓語）','Filipino（菲律賓語）','Español（西班牙語）'],
        default=['English（英語）'],
        help='可選多種母語；每個補充詞依所選語言各提供一個簡短解釋。正文、題目及作答說明仍是繁體中文。')
    if not glossary_languages:
        st.warning('請至少選擇一種補充生詞解釋語言。')
    glossary_language_text='、'.join(glossary_languages)


source=lesson[reference]; gtarget=grammar(lesson,[reference])
known=get_lexicon(idx)
text_evidence, grammar_evidence, original_sentences, grammar_sentences=curriculum_evidence(idx)
attested_phrases=original_sentences+grammar_sentences
target_snapshot = configuration_signature({**target_config, 'reference': reference, 'form': form,
                                             'allowed': sorted(allowed), 'names': sorted(approved_names),
                                             'guides': guiding_words, 'length': target_len,
                                             'theme': theme, 'relation': relation, 'cover': cover})
text_changed = ('audited_text' in st.session_state and
                st.session_state['audited_text'] != st.session_state.get('draft',''))
if st.session_state.get('active_target_settings') != target_snapshot or text_changed:
    had_previous_work = bool(st.session_state.get('last_audit') or st.session_state.get('approved')
                             or st.session_state.get('v2_loaded'))
    invalidate_audit(st.session_state)
    st.session_state['active_target_settings'] = target_snapshot
    if had_previous_work:
        st.info('目標或課文設定已變更，先前檢測與定稿狀態已失效。已保留閱讀文本及編輯內容，請重新檢測並核對命題依據。')
# Keep teacher text and per-lesson settings across source/form/count/lesson changes.
current_key=f'{idx}:{reference}:{form}:{n}'
st.session_state['active_key'] = current_key
target_minimum_text = '；'.join(
    word + '：' + '、'.join(f'第{piece}篇至少{value}次' for piece, value in counts.items())
    for word, counts in target_minimums.items()
)

with st.expander('查看教材來源與複現目標',expanded=False):
    st.write('**原課文：**');st.text(source)
    st.write('**本次目標複現詞（已合併去重）：**','、'.join(target) or '（未選詞）')
    st.write('**本次補充引導詞（教師允許）：**','、'.join(guiding_words) or '未設定')
    st.write('**當課目標語法：**','；'.join(gtarget) or '（原文未載）')
    st.caption(f'截至本課：全課文重建斷詞 {len(text_evidence)} 項（主要已學比對集合）；生詞表 {len(known)} 項（教材證據）；語法例句 {len(grammar_evidence)} 項；整課字數約 {whole_len} 字。')
    st.info('目前 JSON 未保留原始人工校訂的全課斷詞清單；此處從完整課文使用本專案 Jieba 字典重建。若您有原始斷詞 JSON，可再替換。')

st.info('Python逐詞逐篇檢測將確定詞次與可疑候選分開；分篇不明或歧義仍須教師核對。舊版彙總字串次數僅供參考，不代替逐篇結果。')
with st.expander('查看本次逐詞逐篇最低要求', expanded=False):
    st.dataframe([{'目標詞': word, '篇章': piece, '最低次數': value}
                  for word, counts in target_minimums.items() for piece, value in counts.items()],
                 hide_index=True, use_container_width=True)

st.subheader('第一階段｜生成真正的新文本')
with st.expander('生成提示詞',expanded=True):
    prompt1=f'''你是臺灣華語教材編寫者。依下列原課文生成 {n} 篇全新{form}，供{book}{lesson['課']}學生練習。
【原課文（僅參考主題，不可改寫、續寫或照搬）】
{source}
【生成條件】
- 事件目的、關鍵資訊或結果須與原文不同；不能只改姓名、地點或順序。{('情境：'+theme+'。') if theme else ''}
- 教師核准人名／說話者：{'、'.join(approved_names) or '無；不使用人名'}；不可自行新增。
{('- 教師核准引導詞：'+'、'.join(guiding_words)+'。以其意義發展新情節，不強制全部出現，也不自動成為複現目標。') if guiding_words else ''}
{('- 教師一般允許詞：'+'、'.join(allowed)+'；不自動成為複現目標。') if allowed else ''}
- 每篇約 {target_len[0]}–{target_len[1]} 個中文字（不含標題與說話者）；{('各篇各有完整事件、篇幅接近，'+relation) if n>1 else '單篇須有完整事件'}。
- 優先使用截至本課已學詞；必要新詞盡量少，使用臺灣繁體中文。
- 本次目標複現詞：{'、'.join(target) or '無'}；語法：{'；'.join(gtarget) or '無'}。須自然正確，不機械堆疊。
- 主要複現要求：逐詞逐篇最低次數：{target_minimum_text or "未選定目標詞"}。
- 舊版覆蓋偏好（僅供參考）：{cover}；不代表已完成正式逐篇檢測，不得降低上述最低次數。
【輸出前自行核對】核對以上條件，但不輸出逐項分析或檢測報告；Python才是可量化條件的正式檢測依據。
【輸出】僅輸出「【文本一】標題」與正文；多篇依序編號。不得輸出原課文、測驗、詞義、分析、詞彙來源或檢測報告。'''
    format_prompt(prompt1)

st.subheader('第二階段｜貼回文本、Python 檢測、修訂')
st.caption('多篇請各用【文本一】標題、【文本二】標題等獨立標題行，正文另起一行；單篇可直接貼正文。漏篇或分篇不明不以全文合計替代。')
st.session_state.setdefault('draft_lesson_id', idx)
if st.session_state.get('draft','').strip() and st.session_state['draft_lesson_id'] != idx:
    st.warning('保留草稿可能來自先前課次，請確認是否適用於目前課次。此提醒不會刪改草稿，也不阻擋操作。')
st.text_area('將 LLM 產生的閱讀文本貼在這裡（可直接修改）',height=280,key='draft',
             on_change=remember_draft_lesson,args=(idx,))
if st.button('執行 Python 檢測',type='primary'):
    raw=st.session_state.get('draft','')
    if not raw.strip():
        st.warning('請先貼入文本。')
    else:
        report=audit(raw,known,target,source,allowed,text_evidence,grammar_evidence,approved_names,attested_phrases)
        report['逐篇複現']=per_text_target_audit(raw,target_config,known,allowed,text_evidence,grammar_evidence,approved_names)
        header_lines = {piece['header_line'] for piece in report['逐篇複現']['pieces']}
        speaker_body = '\n'.join(line for number, line in enumerate(raw.splitlines(), 1)
                                 if number not in header_lines)
        report['未核准說話者']=speaker_warnings(speaker_body,approved_names) if form=='對話' else []
        st.session_state['last_audit']=report
        st.session_state['audited_text']=raw
        st.session_state['audited_names']=tuple(sorted(approved_names))
        st.session_state['audited_guides']=tuple(sorted(guiding_words))
        st.session_state['audited_target_settings']=target_snapshot
        st.session_state['approved']=False
st.text_area('教師修訂意見', key='revision_notes',
             placeholder='保留人物與結局、調整語氣等；意見不會自動改動最低次數。')
report=st.session_state.get('last_audit')
if report:
    c1,c2,c3,c4=st.columns(4)
    c1.metric('正文中文字數',report['字數'])
    c2.metric('舊版彙總命中目標（參考）',f"{sum(bool(v) for v in report['目標詞複現'].values())}/{len(target)}")
    c3.metric('教材未收錄詞次',report['疑似超綱詞次'])
    c4.metric('與原文相似度',f"{report['整體字元相似度']:.0%}")
    st.caption('先採截至本課的全課文斷詞與課文連續用詞，再對照生詞表、語法例句、歷來已學人名與教師允許詞。未找到的項目仍需核對。')
    st.write('**檢測依據統計：**', '；'.join(f'{k} {v} 詞次' for k,v in report['已學證據分類'].items()))
    per_text = report['逐篇複現']
    st.markdown('**逐詞逐篇檢測（Python）**')
    st.caption('確定詞次須為教材優先斷詞與同一Jieba結果邊界一致的完整詞；可疑候選不算確定詞次。確定不足量＝最低要求減確定詞次（至少0）；有歧義時仍須教師核對最終缺額。缺篇或格式不明以空值及待核對呈現。')
    if per_text['issues']:
        st.warning('分篇／設定或斷詞待核對：' + '；'.join(per_text['issues']))
    if per_text['rows']:
        st.dataframe(per_text['rows'], hide_index=True, use_container_width=True)
    else:
        st.info('目前未設定目標詞，不自動宣稱複現達標。')
    with st.expander('斷詞歧義與候選證據（供教師核對）', expanded=bool(per_text['ambiguities'])):
        st.caption('行號從1起算；行內位置從0起算，結束位置不含該字元。可疑數是候選位置數，不當作正式詞次。')
        if per_text['ambiguities']:
            st.dataframe(per_text['ambiguities'], hide_index=True, use_container_width=True)
        else:
            st.caption('目前沒有可追溯的歧義候選；這不代表分篇格式或所有教學條件均已通過。')

    if report['未核准說話者']:
        st.error('出現未經教師核准的說話者：'+'、'.join(report['未核准說話者'])+'。請先改名、調整允許名單，然後重新檢測。')
    if report['原文連續片段重合率']>.25 or report['整體字元相似度']>.7:
        st.error('與原課文的相似度偏高：請人工確認是否只是在照搬、少量改寫或延續原對話。')
    if not(target_len[0]<=report['字數']<=target_len[1]*n):
        st.info('篇幅提醒：請逐篇檢查是否符合每篇字數範圍；目前顯示總正文概估字數。')
    left,right=st.columns(2)
    with left:
        st.markdown('**舊版彙總字串命中（僅供參考）**')
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
        plan = revision_plan(
            report.get('逐篇複現'), target_config, st.session_state.get('draft', ''),
            st.session_state.get('audited_text'), target_snapshot,
            st.session_state.get('audited_target_settings'))
        notes = st.session_state.get('revision_notes', '')
        if notes.strip():
            st.warning('教師意見若與原設定衝突，請核對後再修訂；系統不會自動判斷一般語意衝突或降低最低要求。')
        if not plan['valid']:
            st.warning('沒有目前有效且完整的逐篇檢測結果，請重新執行 Python 檢測。')
        else:
            if plan['deficits']:
                st.markdown('**正式不足項目（僅明確未達標）**')
                st.dataframe(plan['deficits'], hide_index=True, use_container_width=True)
            elif plan['all_met']:
                st.info('目前選定目標的確定詞次均已達標；這不代表語意、自然度或整體教學品質已通過。可選用一般文字修訂。')
            if plan['reviews'] or plan['issues']:
                st.info('另有待人工核對項目；可疑匹配、未知詞次與漏篇不自動列為補寫要求。請核對上方分篇與歧義證據。')
                if plan['reviews']:
                    st.dataframe(plan['reviews'], hide_index=True, use_container_width=True)
            if plan['issues']:
                st.info('分篇、設定或斷詞有待核對問題，暫不產生修訂提示詞；先確認原文與設定，再重新檢測。已知缺額仍列示，不推定漏篇內容。')
            if not plan['deficits'] and not plan['all_met']:
                st.info('目前沒有可自動要求補足的明確不足項目；請先核對分篇、設定或歧義。不虛構缺額。')
            revision_context = dict(
                source=source, book=book, lesson=lesson['課'], form=form, text_count=n,
                relation=relation, length=target_len, theme=theme, grammar=gtarget,
                names=approved_names, guides=guiding_words, allowed=allowed,
                retained=retained, cover=cover)
            prompt2 = revision_prompt(plan, st.session_state['draft'], revision_context, notes)
            if prompt2:
                format_prompt(prompt2)
                st.caption('提示詞僅代表目前草稿與設定的修訂建議。貼回修訂稿後須重新檢測，再由教師人工確認定稿。')
    st.markdown('**教師確認定稿**')
    st.caption('檢測後如要人工修改，請重新檢測。確認前不能進行命題。')
    st.caption('定稿是教師人工決定，不會把未達標或待核對的逐篇結果自動改成達標；請先核對表格、分篇與歧義證據。')
    names_changed=st.session_state.get('audited_names') != tuple(sorted(approved_names))
    guides_changed=st.session_state.get('audited_guides') != tuple(sorted(guiding_words))
    targets_changed=st.session_state.get('audited_target_settings') != target_snapshot
    if st.session_state.get('audited_text') != st.session_state.get('draft') or names_changed or guides_changed or targets_changed:
        st.warning('文本、核准人名或補充引導詞在上次檢測後變更，請重新檢測。')
    if st.button('將目前檢測內容確認為定稿', disabled=(st.session_state.get('audited_text') != st.session_state.get('draft') or names_changed or guides_changed or targets_changed or bool(report['未核准說話者']))):
        st.session_state['final_text']=st.session_state['draft']
        st.session_state['approved']=True
        st.session_state['quiz_text']=''
        st.session_state['answer_text']=''
        st.session_state.pop('answer_editor', None)
        st.session_state.pop('quiz_answer_source', None)
        st.session_state['approved_retained']=list(st.session_state.get('retained_words',[]))
        st.success('已鎖定此份文本作為命題依據。')

st.divider()
st.subheader('第三階段｜產生閱讀測驗 JSON')
if not st.session_state.get('approved'):
    st.info('請先在第二階段完成 Python 檢測並確認定稿，再產生命題 JSON。')
else:
    final=st.session_state['final_text']
    changes=compare_changes(source,final)
    difference_text='\n'.join(f"- 原：{v['原文片段']} / 新：{v['新文本片段']}" for v in changes) or '無明顯差異'
    retained=st.session_state.get('approved_retained',[])
    st.write(f'命題設定：{quiz_basis}；{quiz_type}；{quiz_qty} 題；重點：{quiz_focus}')
    st.caption(f'教師保留詞會以所選語言（{glossary_language_text or None}）分別提供簡短釋義；原詞仍是繁體中文。')
    if quiz_basis=='依閱讀理解目標':
        change_instruction='依新文本閱讀理解重點命題，不需要參照原課文。'
        difference_section=''
    else:
        change_instruction=('優先針對新文本不同於原課文的關鍵資訊、行動或結果命題。' if quiz_basis=='優先針對新舊文本變化' else '兼顧新文本的內容變化及一般閱讀理解。')
        difference_section='文字差異線索（僅供判斷，不能當成題目答案）：\n'+difference_text
    # 將截至目標課的已學詞彙集合提供給 LLM 自檢；不只是提供冊課名稱。
    # 同時保留 Python 獨立驗證，因為模型宣稱通過不代表實際無超綱。
    known_for_llm=sorted(set(known)|set(text_evidence)|set(grammar_evidence),key=lambda x:(len(x),x))
    llm_allowed=uniq(allowed+approved_names+retained)
    prompt3=f'''你是臺灣華語閱讀測驗教師。先命題、檢核、修訂，最後只輸出一個可由 json.loads 解析的 JSON，不輸出過程。
【定稿閱讀文本：必須原樣保留】
{final}
【已學詞語（截至本課，含課文斷詞、生詞、語法例句）】
{'、'.join(known_for_llm)}
【教師明確允許詞（不限是否已學）】{'、'.join(llm_allowed) or '無'}
【第二階段核准的閱讀補充生詞】{'、'.join(retained) or '無'}
【生詞解釋語言：每種都要提供】{glossary_language_text or 'English（英語）'}。解釋簡短、自然、準確；繁體中文解釋優先用已學詞，其他語言直接用該語言，不強求華語解釋。
{difference_section}
【命題設定】{quiz_type}，{quiz_qty} 題；理解重點：{quiz_focus}；命題依據：{quiz_basis}。
{change_instruction} 只考核定稿文本，不能要求比較原文。題幹、選項、作答說明均用臺灣繁體中文。單選題四個不同選項且只有一個答案；其他題型依類別提供答案。
【生成後必做兩輪自檢；不得只宣稱通過】
第一輪（內容）：逐題核對答案是否能由定稿文本支持、選項有無歧義或多個正解、題數題型是否符合、是否測閱讀理解。發現問題先修改。
第二輪（語言）：逐題檢查作答說明、題幹、每個選項，優先使用上述已學詞語與教師允許詞；不在清單中的詞，先以已學語言簡化，且不能改變題意或答案。
若有未學詞無法合理替換：可必要保留，但必須加入 supplementary_vocabulary，依全部指定語言解釋，source 標為 ["question"]；若閱讀已收錄同一詞，合併成一筆並以 ["reading","question"] 標來源，不能重複。不要無故新增補充詞。完成後再次檢查題目語言、答案及補充詞的一致性。
若有無法保證正確的情況，記錄到 validation_warnings（文字陣列），不得虛稱全部通過。
【唯一輸出：JSON】
{{"title":"閱讀測驗","reading_texts":[{{"title":"文本一標題","content":"原樣保留的完整正文"}}],"supplementary_vocabulary":[{{"word":"保留詞","source":["reading"],"explanation":{{"{glossary_languages[0] if glossary_languages else 'English（英語）'}":"釋義"}}}}],"instructions":"繁體中文作答說明","questions":[{{"type":"single_choice","stem":"繁體中文題幹","options":["A選項文字","B選項文字","C選項文字","D選項文字"],"answer":"A"}}],"validation_warnings":[]}}
reading_texts 需包含全部 {n} 篇定稿文本，不可續寫或改寫。questions 必須有 {quiz_qty} 題；type 使用 single_choice/true_false/short_answer/ordering；沒有選項的題型填 []。選項文字不要附 A. 等標籤。supplementary_vocabulary 解釋的鍵必須包含每一種所選語言完整名稱；若無補充詞填 []。不得輸出 Markdown、語法分析、單詞來源、初稿或檢查紀錄。'''
    format_prompt(prompt3)
    with st.expander('檢視新舊文本差異線索（供教師核對）'):
        if changes:
            st.dataframe([{'原課文':c['原文片段'],'新文本':c['新文本片段']} for c in changes],hide_index=True,use_container_width=True)
        else:
            st.info('未辨識明顯的文字差異。')
    st.info('將提示詞貼到 Copilot 或其他 LLM；把取得的完整 JSON 一次貼入第四階段。')

st.divider()
st.subheader('第四階段｜貼入 JSON、教師編輯、下載 Word')
st.caption('一次貼入第三階段取得的 JSON。程式自動填入閱讀文本、補充生詞、作答說明、題目和教師答案。')
st.text_area('LLM 回傳的 JSON',height=175,key='v2_raw_json',placeholder='貼入包含 reading_texts、supplementary_vocabulary、questions 的 JSON')
if st.button('解析 JSON 並填入以下編輯區',type='primary'):
    try:
        parsed=parse_learning_json(st.session_state.get('v2_raw_json',''))
        # 所有同名補充詞合併，避免閱讀和題目的補充詞重複列印。
        parsed['supplementary_vocabulary']=normalized_glossary(parsed.get('supplementary_vocabulary',[]),glossary_languages)
        edits=json_to_editors(parsed)
        for k,v in edits.items():
            st.session_state[k]=v
        st.session_state['v2_loaded']=True
        st.session_state['v2_show_vocab']=bool(edits['v2_vocab'].strip())
        st.session_state['v22_warnings']=parsed.get('validation_warnings',[])
        st.session_state['v22_json_structure_issues']=inspect_structured_quiz(parsed,quiz_type,quiz_qty)
        st.session_state['v22_json_vocab_issues']=missing_glossary_explanations(parsed['supplementary_vocabulary'],glossary_languages)
        if st.session_state.get('approved'):
            canonical=normalize_text(st.session_state.get('final_text',''))
            returned=normalize_text('\n'.join(t['content'] for t in parsed['reading_texts']))
            if canonical!=returned:
                st.session_state['v22_json_structure_issues'].append('LLM 回傳的閱讀正文與第二階段定稿不完全一致，請檢查是否改寫或漏段。')
        st.success(f"已載入 {len(parsed['reading_texts'])} 篇閱讀文本、{len(parsed.get('supplementary_vocabulary',[]))} 個補充詞及 {len(parsed['questions'])} 道題目。可直接編輯並下載 Word。")
    except ValueError as exc:
        st.error(str(exc))

# 可從目前教師定稿建立閱讀文本初稿，毋須先生成題目。
if st.button('只載入已定稿閱讀文本（暫不命題）'):
    st.session_state['v2_body']=(st.session_state.get('final_text','') if st.session_state.get('approved') else st.session_state.get('draft',''))
    st.session_state.setdefault('v2_title',f"{book}{lesson['課']} 閱讀測驗")
    st.session_state.setdefault('v2_vocab','')
    st.session_state.setdefault('v2_instructions','')
    st.session_state.setdefault('v2_questions','')
    st.session_state.setdefault('v2_answers','')
    st.session_state['v2_loaded']=True

if st.session_state.get('v2_loaded'):
    st.markdown('**可編輯的 Word 內容**')
    title=st.text_input('文件標題',key='v2_title')
    body=st.text_area('閱讀文本（可修改）',height=220,key='v2_body')
    vocab=st.text_area('閱讀前補充生詞及解釋（可修改或刪除）',height=100,key='v2_vocab',help=f'解釋語言：{glossary_language_text}。由第二階段核准詞建立，多語解釋會自動整理成同一筆詞條；教師可直接修改。')
    show_vocab=st.checkbox('在學生版及教師版列印補充生詞',key='v2_show_vocab')
    instructions=st.text_area('作答說明（可修改）',height=65,key='v2_instructions')
    questions=st.text_area('閱讀理解題目及選項（可修改）',height=240,key='v2_questions')
    answers=st.text_area('教師答案（僅教師版印出，可修改）',height=100,key='v2_answers')
    full_quiz=serialize_quiz(instructions,questions)
    # Streamlit 編輯欄位會觸發 rerun：不必另按「檢測」，僅顯示異常。
    glossary_words=[w for w,m in glossary_lines(vocab)]
    q_allow=uniq(re.split(r'[,，、\n]+',st.session_state.get('quiz_allowed_raw','')))
    if questions.strip():
        review_rows=quiz_vocab_review(instructions,questions,glossary_words,known,allowed+q_allow,
                                      text_evidence,grammar_evidence,approved_names,attested_phrases)
        unexpected=[r for r in review_rows if r['尚未解決']!='—']
        covered=[r for r in review_rows if r['已由補充生詞涵蓋']!='—']
        structure_problems=editable_quiz_checks(instructions,questions,answers,quiz_qty,quiz_type)
        structure_problems+=st.session_state.get('v22_json_structure_issues',[])
        if unexpected or structure_problems or st.session_state.get('v22_json_vocab_issues') or st.session_state.get('v22_warnings'):
            st.warning(f'題目檢查：{len(unexpected)} 個區塊仍有教材未收錄詞，{len(structure_problems)} 項格式／一致性提醒。請核對後可自行決定是否匯出。')
            with st.expander('查看自動檢測異常（不妨礙編輯與匯出）',expanded=True):
                if unexpected:st.dataframe(unexpected,hide_index=True,use_container_width=True)
                for issue in structure_problems+st.session_state.get('v22_json_vocab_issues',[])+st.session_state.get('v22_warnings',[]):
                    st.write('• '+str(issue))
        elif covered:
            st.success('題目檢測：未發現未處理詞彙；必要新詞已由補充生詞涵蓋。')
        else:
            st.success('題目檢測：未發現教材未收錄詞及基本結構問題。')
    # 教師仍可選擇查看完整檢測表，但不是必要操作。
    with st.expander('選用：查看完整逐題詞彙比對'):
        if st.button('顯示完整逐題檢測表'):
            q_allow=uniq(re.split(r'[,，、\n]+',st.session_state.get('quiz_allowed_raw','')))
            results=audit_quiz_by_item(full_quiz,known,allowed+q_allow,text_evidence,grammar_evidence,approved_names,attested_phrases)
            if results:
                st.dataframe(results,hide_index=True,use_container_width=True)
            else:
                st.info('目前沒有可檢測的題目。')
            st.caption('結果僅為詞庫比對線索，教師可修改並自行決定保留。')
    if not body.strip():
        st.warning('閱讀正文尚未填入，不能匯出 Word。')
    else:
        if not questions.strip():
            st.info('尚無測驗題，仍可先下載只有閱讀文本的 Word。')
        actual_vocab=vocab if show_vocab else ''
        student_doc=write_docx(body,full_quiz,'',title,actual_vocab,False)
        teacher_doc=write_docx(body,full_quiz,answers,title,actual_vocab,True)
        a,b=st.columns(2)
        with a:
            st.download_button('下載學生版 Word',data=student_doc,file_name='ReadingParallel_學生版.docx',mime='application/vnd.openxmlformats-officedocument.wordprocessingml.document',key='v2_student_download')
        with b:
            st.download_button('下載教師版 Word（附答案）',data=teacher_doc,file_name='ReadingParallel_教師版.docx',mime='application/vnd.openxmlformats-officedocument.wordprocessingml.document',key='v2_teacher_download')
else:
    st.info('請先貼入 JSON 並按解析；也可以先載入第二階段的定稿閱讀文本。')

st.caption('v2.2：LLM 命題後自檢、必要新詞合併補充；第四階段自動進行 Python 詞彙與題目結構檢查，維持 v1.7 Word 排版。')
