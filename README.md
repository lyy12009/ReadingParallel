# 高複現華語閱讀文本工作室 · Python v1.9

## 修正 2026-11-07 的 Jieba ValueError

* 修正 `dict.txt.big` 有非整數詞頻時造成網站無法啟動。
* 啟動時先核對每行 `詞語 數字詞頻 [詞性]`，無效資料列會略過，並在頁面列出範例。
* 字典有效詞條少於 1000 筆，或 Jieba 載入失敗時，改用 Jieba 預設字典，保留所有教材及檢測功能。
* 正常載入時才會顯示「繁體中文大字典已載入」。

## GitHub 部署

根目錄上傳 `app.py`、`corpus.json`、`requirements.txt`、`dict.txt.big`；Streamlit Cloud Main file path 設定 `app.py`。

`dict.txt.big` 必須是 UTF-8 的純文字字典，非 GitHub 網頁。每行範例：`研究 100 n`。注意數字是詞頻，不是課次。亦可將字典放在 `dictionaries/dict.txt.big`。大字典的版權及來源請依原始發行條款確認。

版本依舊以教師複製 Prompt 到外部 LLM 並貼回的工作流程運行，不需要 LLM API Key。檢測報告屬疑似超綱，須人工覆核。
