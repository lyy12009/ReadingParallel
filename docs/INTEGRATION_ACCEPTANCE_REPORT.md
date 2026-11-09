# ReadingParallel 第六階段整合驗收報告

日期：2026-11-09。狀態：**可進入教師人工驗收；尚未完成教學品質、真實瀏覽器與Word視覺驗收。**

## 基準與本次範圍

- Repository：lyy12009/ReadingParallel；[PR #6](https://github.com/lyy12009/ReadingParallel/pull/6)已確認closed／merged。
- 已執行git fetch origin並明確同步origin/main；main基準為3fc00d01a1bbb79f8b692d07518d26d582af01ec。
- 起始工作樹乾淨，從該main建立codex/integration-acceptance，不移動本機main，不覆寫舊分支或教師資料。
- 本次僅新增本報告、[教師操作清單](TEACHER_ACCEPTANCE_CHECKLIST.md)及[離線驗收重播腳本](../tests/acceptance_replay.py)。不修改產品Python、教材、詞典、既有測試、依賴或教學規則。
- 已閱讀AGENTS.md、DEVELOPMENT.md、README.md、兩份教學／測試規格，以及五個產品Python模組、既有六個test檔與兩個smoke腳本。另檢視start.bat、requirements與字典目錄說明；未執行會安裝套件的start.bat。

## 本次實際執行與證據界線

| 檢查 | 本次結果 | 可證明／不能證明 |
|---|---|---|
| unittest discover | 原有114項全部通過，25.100秒 | 包含純邏輯、真實Jieba／教材、AppTest及固定離線JSON／Word；不是所有教材或所有教學品質驗證 |
| smoke_streamlit.py | 通過 | 初始頁→貼文→Python檢測→模擬教師定稿→第三階段提示詞 |
| smoke_server.py | 通過 | 真實本機127.0.0.1健康端點與首頁HTTP 200，測後停止程序；不是瀏覽器互動或Cloud |
| acceptance_replay.py | 三案全部通過，六份Word產生並讀回檢查 | 額外的離線AppTest操作重播，不計入114項，不是LLM輸出或真實教師審定 |
| pip check | No broken requirements found | 現有環境依賴一致；未安裝或升級 |
| 語法、文件、Git檢查 | 通過：Python語法、文件連結／圍欄、diff --check與新增範圍；所有既有追蹤檔均與main相同 | 不以靜態檢查代替上述實跑 |
| 瀏覽器、Word視覺、教師內容審閱、外部LLM、Q13/W08 | 未執行／待人工 | 不宣稱已完成 |

環境：專案.venv的Python 3.9.9、Streamlit 1.50.0、jieba 0.42.1、python-docx 1.2.0。既有use_container_width棄用提醒仍出現，未擴大改動API。

在專案根目錄執行：

~~~powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -B tests/smoke_streamlit.py
.\.venv\Scripts\python.exe -B tests/smoke_server.py
.\.venv\Scripts\python.exe -B tests/acceptance_replay.py
git diff --check
~~~

本機記錄位於test-results/acceptance-full-tests.log、acceptance-smoke-streamlit.log、acceptance-smoke-server.log、acceptance-replay.log。重播會產生test-results/integration-acceptance/evidence.json及A／B／C子目錄的原文、最終文、JSON與兩版Word。這些檔案全部忽略、不提交；重播腳本及固定樣本受Git管理，換機可重新產生。重播會更新自己的同名驗收產物，不觸及教師專案資料。

資料SHA-256於重播前後一致：

- corpus.json：6d51aa60a30419a7bd6b7dc913b075a594197597867ce65735c15b9b41efedd2
- dict.txt.big：192c9657109d15688865c85b2b1359ec3798cf73e8fe48b5bfd97d49622e3bb9

## 四批資料串接與Session State

| 階段／定位（基準main） | 資料與一致性 | 本次證據 |
|---|---|---|
| 選詞：[app.py](../app.py) words()、側欄約829行；[target_settings.py](../target_settings.py) combine_targets()/minimum_matrix() | 全課各部分生詞候選；實際選詞與手動目標合併去重；target_settings_by_lesson按DATA全域課索引保存，target_config只含當前活躍格。目標身份不修改已學／允許身份 | 114項含跨課／冊、來源切換、恢復個別值；A用文章1作來源仍勾對話1生詞，C用手動前課咖啡 |
| 生成前摘要：app.py約969–999行；[generation_constraints.py](../generation_constraints.py) summarize_constraints() | 直接讀目前target_config及篇幅／context，不讀last_audit，不快取警示；詞數與總詞次分開，A/B/C不改設定、不擋提示詞 | 三案摘要分別2、2／3、1最低詞次；提示詞仍可見，設定重播前後不變 |
| 正式檢測：app.py per_text_target_audit()、約1026行；[vocabulary_audit.py](../vocabulary_audit.py) audit_per_text() | 共用教材優先詞庫與同一Jieba；last_audit['逐篇複現']區分確定、可疑、未知與逐格最低值，不從舊彙總推正式缺額 | A確定達標；B僅第2篇照片缺1；C確定0、可疑1、待核對 |
| 有效性：app.py約932–945行、remember_draft_lesson()；invalidate_audit() | 文本／設定變更清報告及快照並撤銷approved；保留draft、final_text歷史、v2_body與revision_notes | B貼回修訂稿後舊提示與報告消失；三案匯出後調最低值，舊結果失效且兩份文字保留 |
| 針對性修訂：app.py約1094行；[revision_prompts.py](../revision_prompts.py) revision_plan()/revision_prompt() | 只接受當前快照與逐篇正式結果；缺額只列明確未達標。歧義另列，格式問題不補造缺篇；教師意見不改最低值 | B提示只要求第2篇照片再1次；C沒有強制補詞提示；A／B達標後一般修訂不等於品質通過 |
| 人工定稿：app.py約1127–1143行 | approved是教師按鈕的決定，不把未達標／待核對列改成通過；approved_retained是按定稿時的保留詞快照 | C模擬按定稿後仍待核對；這不代表歧義已裁決 |
| 命題與JSON：app.py prompt3、parse_learning_json()/json_to_editors()約1198行 | 以final_text命題；JSON回填v2_*。核對結構、釋義完整度及簡化正文一致性，不自動證明答案語意 | 三案各3題，固定A/B/C有文本證據；全部JSON解析且無載入結構／釋義缺語言警告 |
| 超綱與Word：app.py quiz_vocab_review()、約1230–1289行及write_docx() | 題幹／選項／說明隨編輯重檢；保留詞不等於已學。學生版不印答案，教師版印答案，補充詞列印可切換 | 三案插入額外未知說明後核對表更新；B/C兩語補充詞列印開關皆驗證；六份Word正文、題目、答案隔離與A4檢查通過 |

target_snapshot包含target_config、來源、形式、允許詞、人名、引導詞角色、篇幅、情境、多篇關係及舊覆蓋偏好。修改其中項目會保守重檢；單純修改revision_notes不改詞次設定，提示詞重新組合。Session State是單一工作階段暫存，不是已實作永久教師紀錄。舊final_text被保存不代表目前設定仍核准。

## 三組真實課次的實際結果

完整操作、樣本全文、人工核對與題目證據見教師清單。這些是工程驗收固定樣本，不是宣稱符合該課全部語言難度／語法目標的可直接授課教材。

| 案例 | 真實教材依據 | 初始結果與後續 | 字數（只計正文漢字） |
|---|---|---|---|
| A／詞次正常達標 | DATA[30]第三冊第一課「老師嚴，才學得好」，來源文章1；新生、壓力列於對話1生詞 | 兩詞最低各1，確定各1、可疑0；一般修訂；模擬定稿→3題JSON→學生／教師Word | 122 |
| B／部分不足 | DATA[1]第一冊第二課「我的家人」，來源對話1；照片、妹妹列於該部分生詞 | 兩篇；照片最低1／2，妹妹各1。照片確定2／1，僅篇2缺1；修訂篇2後照片2／2，四格達標；重檢及重新定稿後匯出 | 初始112／112；修訂後112／123 |
| C／歧義待核對 | DATA[15]第二冊第一課「請問，到師大怎麼走？」，來源文章1；手動咖啡可追溯DATA[0].對話2生詞 | 最低1，咖啡豆內咖啡確定0、可疑1。教材優先為安同｜想｜買｜咖啡｜豆，Jieba為安｜同｜想｜買｜咖啡豆；只提醒、不強制補詞。模擬人工按定稿並匯出，狀態仍待核對 | 119 |

C歧義證據：原始第2行，行內[4,6)為「咖啡」，片段「安同想買咖啡豆」。這是程式的可追溯候選，不是教師已裁定可計1次。沒有將「咖啡豆」自動改為「咖啡」或改字典。

三案均仍存在疑似超綱：A有7種、B修訂後75種、C有13種；載入題目後每案4個區塊仍有待處理詞彙。B/C固定JSON只示範禮物／豆的雙語解釋，並未覆蓋所有未知詞。**尤其B的語言超出第一冊第二課程度甚多，不能因詞次達標或Word可匯出就直接用於教學。**教師須簡化文本／題目、核對教材證據或決定少量補充詞，再重新檢測。語法正確性、故事新穎與自然度沒有自動驗收。

## 問題、重現與影響（只記錄，未修產品）

| ID／分類 | 重現方式與實際觀察 | 影響與處理 |
|---|---|---|
| IA-01／已知未完成、實際重現 | 任一案載入JSON後，在v2_body末尾加「獨角獸太空港。」；draft與audited_text仍為舊文，approved仍true，未重算逐篇目標。三案均觀察到 | 第四階段Word正文與第二階段定稿可不同；目前不能用舊表保證輸出正文達標。教師不要把編輯後正文當已重檢；另保存編輯稿，回第二階段貼入、重檢、定稿，重新命題／核對題目，再載入匯出。需後續授權修復D06/Q07/Q08 |
| IA-02／既有人工權限、非新增錯誤 | C有待核對仍可按定稿，approved變true但列仍待核對 | 按鈕不是數值通過／人工詞界裁決。教師須獨立記錄歧義處理；目前無裁決介面，不自行加阻擋 |
| IA-03／教學待驗收、非演算法故障結論 | 三案仍有超綱提示及題目未知區塊，B尤多；提示不阻擋Word | 固定樣本僅驗證警示與流程。未完成教師難度審核；不能以匯出成功宣稱超綱已控制完成 |
| IA-04／靜態文件落差 | README仍稱v1.9，部署清單只列app.py等舊檔；目前app.py匯入target_settings、vocabulary_audit、revision_prompts、generation_constraints | 文件未列完整程式依賴，照舊清單只複製檔案可能缺模組。本次不改README、不測Cloud部署 |
| IA-05／既有警告刷新限制、靜態檢查 | app.py將JSON載入的v22_json_structure_issues／v22_json_vocab_issues保存，編輯時仍併入舊提醒 | 不將舊提醒當成已依目前全部欄位重新驗證。D07既已記錄；本次未新增負向重現或擴修 |
| IA-06／相容性待辦 | 測試執行仍出現Streamlit use_container_width棄用訊息 | 當前1.50.0測試通過；未測其他依賴版本，不逕升級／重寫 |

未發現114項或三案技術流程的新失敗；以上觀察不是已修復宣告，也不是完整缺陷清單。

## 待辦與人工驗收完成條件

- 人工歧義裁決介面、裁決保存及重算：Q03/Q04/Q11未定，不自行把候選計確定。
- Word編輯正文後完整重檢與核准同步：IA-01、D06/Q07/Q08未完成；答案／釋義的語言檢查範圍仍待決。
- Q13／W08：可行性判準、教師標註樣本、警示敏感度、誤報／漏報與真實生成效果均待實測；B只是一般提醒。
- 教師逐題答案證據、唯一語意正解、難度、語法及翻譯正確性；程式只對固定題目做證據／字串斷言，不能外推所有題目。
- 瀏覽器實際操作、複製提示詞、下載檔案、Word字型／分頁／長選項與多語解釋視覺驗收。
- 本機教師需依清單逐項填寫通過／失敗／待決與原文證據；未完成項不填已通過。本報告不構成部署核准。

下一步為教師執行操作清單並回報IA／Q編號及重現材料；後續修正與遠端操作另待授權。本次僅本機Commit，不Push、不建立PR、不合併main、不部署。
