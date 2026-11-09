# ReadingParallel 測試規格

## 基準、分類與執行邊界

基準：2026-11-09，origin/main aa900a2e9c57f26634c0b490301fe1a36b058d20。教學方向與決策 ID 見 [TEACHING_RULES.md](TEACHING_RULES.md)，開發限制見 [AGENTS.md](../AGENTS.md)。

本次只編寫規格，既有測試保持不變。下列「規劃」不代表已有測試或已通過；「待教師確認」不設定最終預期結果，也不在本階段新增測試或改程式。

分類：
- **技術回歸**：純函式／格式與錯誤處理。
- **AppTest 整合**：執行真實 Streamlit 應用與 Jieba，但不是瀏覽器 GUI。
- **離線固定資料**：模擬外部 LLM 回傳 JSON，沒有模型 API，也不能代表模型品質。
- **實際本機 HTTP**：啟動真實 server，檢查首頁及健康端點；單獨通過不代表 app 已完整執行。
- **人工驗收**：一般自然語言題目語意、釋義、教學品質、Word視覺與瀏覽器下載。

## 一、既有9項自動測試

[tests/test_regressions.py](../tests/test_regressions.py) 以 AST 擷取實際函式與提示詞，不完整匯入 app.py：

| ID | 方法 | 現有檢查 |
|---|---|---|
| E01 | test_stage_three_prompt_renders | 執行實際 prompt3 f-string，確認不再引用未定義變數，包含正文及題型 |
| E02 | test_invalid_reading_title_is_validation_error | title 為數字、null、陣列、物件時，預期 ValueError |
| E03 | test_invalid_question_type_is_validation_error | type 為陣列、物件、null、數字時，預期 ValueError |
| E04 | test_valid_json_and_editor_conversion | Markdown包裹合法JSON、結構檢查、正文／題目／答案轉換 |
| E05 | test_optional_fields_keep_existing_defaults | 缺省文本title及題型仍保持原有預設 |
| E06 | test_all_supported_question_types | 四種題型合法答案的基本結構檢查，非各題型完整GUI測試 |
| E07 | test_unknown_string_type_remains_structure_warning | 未知字串題型可解析，但產生結構提醒 |

[tests/test_integration.py](../tests/test_integration.py) 使用真實套件與應用：

| ID | 方法 | 現有檢查 |
|---|---|---|
| E08 | test_dictionary_and_curriculum | 真實主字典初始化、指定中文句可斷詞且拼回原句、45課對話1非空 |
| E09 | test_full_offline_workflow_and_word | 初始頁→貼文→audit→定稿→prompt3→固定JSON解析→兩版Word；檢查字典載入、說話者、你好複現、欄位及Word內容、A4與答案隔離；教材／字典雜湊不變 |

E09 固定正文為「明華：今天上課。／月美：你好。」；三題分別問明華所說活動、說「今天上課」的人、月美說的話。正解 A「上課」、B「明華」、C「你好」。
測試由該對話推導預期答案，驗證四個不同選項、正解文字恰好出現一次及答案代號指向正解。這是固定樣本的證據驗證，不能判定任意新題的唯一正解。
Word檢查是程式實際產生的位元組，下載函式加上擷取觀測後仍呼叫原函式；未以假Word取代。

另有不計入9項的兩個冒煙腳本：

- [tests/smoke_streamlit.py](../tests/smoke_streamlit.py)：AppTest 驗證初始頁、貼文、檢測、定稿與命題提示詞。
- [tests/smoke_server.py](../tests/smoke_server.py)：僅監聽127.0.0.1，健康端點內容ok且HTTP 200，首頁HTTP 200且包含HTML；finally停止自己啟動的程序。

## 二、已確認方向的量化案例規劃

以下均為後續案例設計，不改動本階段測試。選樣必須記錄 corpus 陣列索引、冊課、欄位與原句，固定字典版本。不能為符合預期而改教材。

| ID／規則 | 輸入與操作 | 可量化預期 | 狀態／實作定位 |
|---|---|---|---|
| P01／T01 | 從前課挑一個生詞 w，確認不在當次target；單獨用 w 呼叫audit | w 不出現在疑似超綱，且不被新增為當次target | 規劃；get_lexicon:180、audit:274 |
| P02／T02 | 對每課計算四部分生詞去重聯集，逐課累積 | get_lexicon(i) 等於目前DATA前綴聯集，集合隨i不減 | 規劃的現行行為特徵測試；當課範圍須Q01確認，不能當作教師已認可 |
| P03／T02 | 從前課課文挑不在生詞表、但存在重建text_evidence的完整token | audit給課文證據、不因缺少當課生詞身份而列未知 | 規劃；lesson_segmented_corpus:210；「出現即已學」細則待Q01 |
| P04／T03 | 前課明確說話者在後課known_names候選內；教師選入approved_names，再放入正文及對話標籤 | 可選歷來名字；核准後不報未核准說話者，正文該名字不列未知 | 規劃；known_names:194、speaker_warnings:250；自動允許待Q02 |
| P05／T04 | 人工target={咖啡,茶}；正文「咖啡，咖啡，茶」；以分隔完整詞避免拆分爭議 | 次數咖啡2、茶1；出現過的目標項數2／2，不能把次數3當覆蓋分子 | 規劃；audit:295、介面892行；這不是門檻決策 |
| P06／T05 | 人工allow及其他證據皆無一個選定未知多字詞；同題在有／無補充詞集合下重檢 | 無補充時待核對；加入後顯示已涵蓋，但get_lexicon資料不變 | 規劃；quiz_vocab_review:744；不宣稱缺釋義已適合教學 |
| P07／T06 | 一個詞提供完整兩語釋義，再移除其中一語 | 完整時missing_glossary_explanations無警告；缺語言時只列該語言 | 規劃；函式760行；翻譯正確性人工驗收 |
| P08／T05/T06 | 同詞分兩筆，來源分reading/question，不同語言解釋 | normalized_glossary輸出一詞，來源聯集且保留兩語 | 規劃；函式688行；同語言不同釋義如何衝突待Q06 |
| P09／T07 | E09固定對話三題，故意刪掉正確選項或改答案代號 | 現有證據／唯一性斷言失敗；恢復正確資料後通過 | E09已有正向與斷言；先前確曾重現舊資料第2/3題失敗；不新增失敗fixture至產品 |
| P10／T07 | 合法JSON中題數與設定不同、少選項、重複選項、答案不在A–D | inspect_structured_quiz回報對應結構提醒 | 規劃補負向覆蓋；不等於語意唯一正解 |
| P11／T08 | 固定題幹／作答說明／某選項各放一個已確認未知詞，其他內容保持已學 | 逐區塊報出未知；補充詞加入後重新計算涵蓋差異 | 規劃；audit_quiz_by_item:559、quiz_vocab_review:744；避開人名或複合詞爭議 |
| P12／T08 | JSON載入後在題目編輯區新增上述未知詞並rerun | 自動檢查以目前題目內容顯示新增未知詞，不只沿用LLM宣稱 | 規劃的AppTest擴充；1048行起 |
| P13／T09 | E09解析後讀回學生／教師Word，保留A/B/C答案 | 兩版正文與題目存在；教師版有完整答案，學生版無各答案行 | E09已覆蓋；不推論排版無問題 |
| P14／T06/T09 | 教師多選兩語、完整釋義，匯出兩版 | 兩份文件皆有兩語解釋；取消列印後依現行UI兩份皆無生詞區塊 | 規劃；show_vocab:1041、write_docx:355；取消列印是否適合學生待Q06 |

## 三、目前落差的未來回歸設計

這些案例是為後續授權修復建立重現條件，本階段不修改程式、不宣稱已通過。

| ID／差異 | 重現操作 | 可驗證的觀察或待決結果 |
|---|---|---|
| G01／D04 | 目標「是」，正文「是不是學生」 | 記錄現行子字串次數2；完整詞的期望待Q04，不先寫新教學斷言 |
| G02／D04 | 多篇一篇覆蓋高、一篇無目標詞 | 現行只彙總；逐篇邊界、分母與門檻待Q04後再設斷言 |
| G03／D06 | 定稿含不同班級英文字母、時間數字、說話者，再比較JSON正文 | 現行normalize_text可能相同；應呈現哪些變動、是否阻擋依Q08界定 |
| G04／D07 | draft前後空白→檢測→按定稿；或檢測後修改一般allow | 記錄strip比較及漏快照；後續修復應驗證快照與實際輸入一致 |
| G05／D07 | JSON初有缺語言／結構問題，再於編輯區修正 | 記錄舊警告仍沿用；規劃日後測試以當前內容重算，不將舊提醒當現況 |
| G06／D03 | 補充詞存在但解釋空白／缺語言／取消列印 | 可同時觀察「詞已涵蓋」與釋義缺漏；匯出警告或阻擋先待Q06 |
| G07／D08 | 不定稿直接貼JSON；已載舊題目後按只載閱讀文本 | 記錄目前允許及setdefault保留舊內容；合適流程待Q08 |
| G08／D02/D10 | 「我是老師」「這是我的書」等句型、一般冒號敘述 | 記錄人名候選誤擷取與正文前綴清理；教師核准正反例後才訂語意預期 |
| G09／D09 | 在答案或釋義加入未知詞，但題幹／選項保持已學 | 記錄目前未同等重檢；是否納入由Q07決定 |
| G10／D11 | 多課、多篇、不同題型及長篇多語Word | 作參數化擴充與人工排版樣本；不以E09單一流程視為全部覆蓋 |

複合詞拆分（Q03）、功能詞例外（Q05）、逐篇複現率門檻與空目標集合（Q04）都標為「待教師確認」。測試可記錄現行行為，但不得把它們固定成新的教學正確性標準。

## 四、人工驗收

| ID | 檢查項目 | 驗收記錄方式 |
|---|---|---|
| M01 | 每題答案由哪句／哪些句支持；推論是否合理 | 教師逐題記錄原句、答案與推論理由；證據不足退回修題 |
| M02 | 單選題只有一個語意正解，干擾項不歧義 | 教師逐選項標註支持／不支持原因；四個不同字串不能取代此檢查 |
| M03 | 已學／超綱、複合詞、人名與功能詞爭議 | 標註詞、課次、原句、斷詞結果與Q決策；未決項不由Codex定奪 |
| M04 | 多語釋義準確、自然且符合上下文 | 具相應語言能力者逐詞覆核；非空不等於正確 |
| M05 | 文本自然度、情節新穎、語法與閱讀難度 | 教師依Q09形成的規準記錄；不以字元相似度替代 |
| M06 | Word字型、頁面、長選項跨頁、多篇與生詞閱讀性 | 以實際Word軟體檢視兩版，記錄頁碼與畫面問題 |
| M07 | 真實GUI輸入、修改、下載與學生／教師檔案 | 瀏覽器實際走一次並開啟下載檔；AppTest不能代替此項 |

人工項目本階段未執行。沒有呼叫LLM或部署Cloud；HTTP只驗本機server。

## 五、執行方法與本階段驗證

沿用專案 .venv，已驗證 Python 3.9.9、Streamlit 1.50.0、jieba 0.42.1、python-docx 1.2.0。依賴範圍未鎖定，未來版本變化需重新驗證。

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -B tests/smoke_streamlit.py
.\.venv\Scripts\python.exe -B tests/smoke_server.py
git diff --check
```

本文件建立後須實際重跑上列命令；執行結果記於交付報告，不能以本規格取代測試紀錄。確認僅AGENTS.md與兩份docs為Git差異，其他既有追蹤檔與origin/main相同。測試產生的test-results與快取保持忽略，不提交。

文件完整性檢查：三份皆為UTF-8、內部檔案連結可解析、T01–T09已確認方向皆有對應、Q與D引用存在；每個自動化規劃有輸入與預期，未實作項目清楚標示。未來更新函式或欄位時同步更新本文件與實際測試，不只改通過數量。
