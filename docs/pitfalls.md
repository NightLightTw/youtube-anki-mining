# 技術雷區

開發時實際踩過的問題。多數已經在程式裡處理掉，這裡記下原因，免得之後有人把那段「看起來多餘」的程式碼刪掉。

## AnkiConnect

- **`sync` 一送出就回傳**：新版外掛只花約 0.06 秒就回傳，同步在背景跑，這段期間任何碰到牌組的呼叫都會回 `Collection lifecycle operation is active.`。要等同步結束請用 `anki.sync()`，不要直接 `invoke("sync")`。這個 API 只等得到「同步結束」，等不到「同步成功」。
- **重複檢查要帶 `Sentence`**：新版的 `canAddNotes` 會先檢查卡片正面是不是空的。卡片正面是 `{{Sentence}}{{SentenceAudio}}`，只帶 `Word` 去問的話，每個字都會被判成不能新增。真正的重複只比對第一個欄位 `Word`。`mine.py` 改用 `canAddNotesWithErrorDetail`，只有錯誤原因明確是「重複」才跳過，其他原因直接報錯；舊版外掛沒有這個 action 時才退回 `canAddNotes`。
- **搜尋快照有上限**：外掛最多保留 100 個還沒讀完分頁的搜尋快照，600 秒後過期。正常跑完的 `findNotes` 不會留下快照，但實際用的時候遇過 `Search snapshot capacity exceeded`，原因還沒查清楚。可以先等十分鐘再試；一直發生的話就要另外排查（單次搜尋結果超過一百萬筆也會回同樣的錯誤，那種等再久也沒用）。
- **外掛會自動更新並重啟 Anki**：更新後第一次呼叫會 `Connection refused`。等 Anki 重新開好再跑一次就好，還沒建的卡不會受影響。
- **`addNote` 可能不理 `deckName`**：卡片會落在「預設」牌組，所以 `mine.py` 建卡後會再用 `changeDeck` 搬回來。
- **媒體檔名要小寫**：Anki 會把媒體檔名轉成小寫，而 iPhone 的檔案系統分大小寫，檔名不一致時圖和音檔會失效。

## Merriam-Webster

- **只讀 `shortdef`，不自己解析 `def → sseq`**：那棵樹裡混著交叉引用（"see also"、"opposite"），自己解析會漏掉主要定義又抓進雜訊。代價是 `shortdef` 沒有義項編號，也沒有例句可以對回去。
- **`: such as` 結尾的半截定義**：有些 `shortdef` 的第一條以 `: such as` 結尾，那其實是分節標題，完整的意思在下一條。目前管線沒有處理。
- **同義詞常來自另一個義項**：同義詞來自另一本字典（thesaurus），`fetch_synonyms` 一律取第一組，兩本字典的義項沒有對應關係。所以定義是「球拍」、同義詞卻是「噪音」這種事很常見。
- **片語定義在 `dros`**：像 `dedicate to` 這種片語的定義放在 `dros`，程式目前只讀 `shortdef`，所以拿不到（見 #3）。
- **衍生詞沒有獨立詞條**：`humbly`、`vulnerability` 這類字掛在母詞底下，管線會改用母詞的定義並標明來源（見 #2）。
- **英式拼法**：`grey`、`colour` 這類字的 headword 對不上，管線改用 `cxs` 和 `meta.stems` 找本體。這條路刻意收得很窄，原因見 README「已知限制」第 3 點。
- **`meta.syns` 可能是空陣列**：取 `[0]` 前要先檢查。

## 詞形還原（simplemma）

- **會還原錯，靠 `LEMMA_OVERRIDES` 逐字修**。錯成罕見真字的最麻煩，因為詞頻篩選擋不住：`crosses → crosse`（長曲棍球棒）、`crew → crow`、`wedding → wed`。這些字原本的詞頻高於上限，還原錯了反而鑽過篩選，變成一張卡。
- **不是每個錯都該覆寫**：`dove`、`lit` 不會被還原成 `dive`、`light`，原樣留下就變成卡片的目標字。但它們本身也是合法的字（鴿子；俚語的「很嗨」），覆寫成動詞原形會弄壞另一半的情況，所以沒有加進覆寫表，遇到時人工處理。
- **目前還沒修的**：`opera → opus`、`weed → wee`。
- **口語黑名單 `NON_LEARNING_WORDS` 不能放有正當意思的字**：`til`（芝麻）、`cos`（餘弦）都是真字，放進去會誤殺。

## 音檔切點

- **json3 逐字時間戳最準**：只有 YouTube 自動字幕才有。人工字幕只有整段的起訖時間，句子邊界要靠線性推算，誤差可達 ±2.6 秒（見 #1）。
- **有逐字時間戳時不做靜音吸附**：實測吸附反而會把已經夠準的邊界推歪（見 #5）。`--legacy-snap` 可以退回舊行為。
- **不要加固定的前後緩衝**：舊版切檔時額外加的緩衝，跟上游的緩衝疊加後會吃到下一句，已經移除。
- **少數影片的 AAC 音軌**會讓 libmp3lame 報 `inadequate AVFrame plane padding`，所以 `extract_audio` 加了 `-af aresample=async=1`。
- **檢查結尾有沒有被切斷，看最後 60ms 的音量**：不要用 `silencedetect`，它有最短靜音長度門檻，會把正常的結尾誤判成被切斷。

## 拿 whisper 當尺時的限制

- **±180ms 以內不可信**：同一個字換不同上下文量，起點會差到 180ms，比要量的切點差異還大。要量更細的差異，得用 forced alignment。
- **被切掉起音的字會被聽成多一個字**：開頭被削掉 0.1 秒的 "Every..." 被轉成 "**And** every..."，看起來像切太前面，其實是切太後面。判斷方向要看音量剖面。
- **small 模型會在句尾的安靜處幻聽**：同一段音檔，small 轉出 "...a definitive list in the future."，medium 轉出 "...a definitive list."。結尾多字又消不掉時，先用 medium 交叉確認，不要把真正的字尾切掉。
- **base 模型會高估問題**：削掉 50ms 起音，它可能報成整組詞都不見了。

## yt-dlp 與字幕

- **單一字幕變體被 429 擋下，會中止整個字幕下載**：yt-dlp 預設在第一個失敗的字幕就停，所以 `run.sh` 加了 `--ignore-errors`，改抓其他英文變體（例如 `en-orig`）。`tests/test_ytdlp_contract.py` 鎖住這個行為。
- **`Sign in to confirm you're not a bot` 通常是出口 IP 被標記**，不是 yt-dlp 太舊。拿一支以前下載成功過的影片測：舊影片也被擋，就是 IP 的問題，換網路才會好。確認被擋後別一直重試，會被擋更久。
- **json3 校正比例偏低時，先懷疑字幕不是逐字稿**：`mine.py` 會印「已用 json3 逐字時間戳校正 X/Y 句」。這個比例比的是 SRT 和 json3 兩份字幕的文字，不是音檔本身，所以只是排查線索。正常影片多半接近 100%；實測有一支只有 171/515，抽聽後確認那支的英文字幕是改寫過的，例句和實際語音對不上。
