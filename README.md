# ReadingParallel Python v1.1 — Streamlit Community Cloud

## 雲端部署

1. 將 `app.py`、`corpus.json`、`requirements.txt` 上傳到 GitHub 儲存庫根目錄。
2. **同層需保留您已上傳的 `dict.txt.big`**，或將其放在 `dictionaries/dict.txt.big`。啟動時自動載入，不會線上下載。
3. 在 Streamlit Community Cloud 建立 App，選擇該儲存庫、分支及 `app.py` 作為 Main file path。
4. 首次執行確認畫面頂端顯示「繁體中文大字典已啟用」。

本 ZIP 不重新散布您 GitHub 儲存庫已存在的大字典；請沿用 https://github.com/lyy12009/article_adjustment/blob/main/dict.txt.big 。若解壓到新的儲存庫，需自行將檔案複製至上述任一路徑。否則程式仍可執行，但會顯示警告並使用 Jieba 預設字典。

## 使用流程

側邊選擇冊課與來源篇章 → 複製 Prompt 至 LLM → 貼回新文本 → Python 檢測 → 教師定稿 → 複製命題 Prompt → 貼回題目並檢測 → 匯出 Word。

## 注意

- 教材先前累積生詞是教學詞彙白名單，不等於語料的完整詞彙與所有已學用語。
- 「疑似超綱」需人工確認；教材詞庫最長匹配也可能遇到歧義。
- 原文相似度、複現率均為字面統計，無法取代教師對詞義、語法功能與自然度的審查。
- 本程式不呼叫 LLM API；適合不需要 API Key 的校內或公開教師使用。
