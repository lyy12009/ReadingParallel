# 本機開發與測試

以 main 為基礎建立開發分支，在開發分支完成修改、測試與 commit；main 的合併與遠端 push 另行確認。

## Windows PowerShell

```powershell
py -3.9 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -B tests/smoke_streamlit.py
.\.venv\Scripts\python.exe -B tests/smoke_server.py
.\.venv\Scripts\python.exe -m streamlit run app.py
```

已驗證 Python 3.9.9、Streamlit 1.50.0、jieba 0.42.1、python-docx 1.2.0。requirements.txt 保留既有版本範圍，並非鎖定檔。使用專案虛擬環境，避免共用環境套件內容與版本資訊不一致。

## 測試範圍

- `test_regressions.py`：7 項回歸測試，含提示詞執行、JSON 型別驗證與合法資料的相容性。
- `test_integration.py`：2 項整合測試，使用真實 Jieba、教材、Streamlit AppTest 與 Word 產生函式。
- `smoke_streamlit.py`：貼文、檢測、定稿與第三階段提示詞的 AppTest 冒煙測試。
- `smoke_server.py`：啟動只監聽 127.0.0.1 的真實伺服器，檢查健康端點與首頁 HTTP，結束後停止該伺服器。

整合測試使用固定離線 JSON，不呼叫外部 LLM。Word 會寫入被忽略的 `test-results/`，測試檢查文件結構與答案隔離；瀏覽器實際操作、Word 視覺排版及教學內容仍須人工驗收。

## 提交前

```powershell
git status --short
git diff --check
git diff
```

只加入預期修改的檔案，檢查 staged diff 後再 commit。`.venv/`、`backups/`、`test-results/`、快取與本機秘密設定不應提交。不要把 API 金鑰寫進程式或教材。
