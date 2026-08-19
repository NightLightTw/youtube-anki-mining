# YouTube → Anki 英文單字挖掘系統

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Platform](https://img.shields.io/badge/一鍵流程-macOS-lightgrey)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)

**貼一個 YouTube 網址，自動把影片字幕變成帶真人發音的 Anki 單字卡。**

![成品卡片示範](docs/card-demo.png)

*↑ 實際成品：例句標色目標字、Merriam-Webster 英英定義、繁中釋義、同反義字、從影片剪出的句子發音、可回看的時間戳連結——全部自動生成。*

## ✨ 核心特色

- **一鍵全自動**：`./run.sh "YouTube網址"`，下載字幕影片 → 挑生字 → 製卡 → 同步 AnkiWeb 一次跑完
- **聰明挑字**：自動排除你 Anki 牌組裡已有的字，用詞頻鎖定「該學但還不會」的區間，只挑語境乾淨的例句
- **內容齊全**：權威字典定義、繁中釋義、同反義字、真人發音 mp3，不是裸字直翻
- **可手動精修**：也支援自己挑句子挑單字的手動模式

## 目錄

- [這是什麼？](#what-is-this)
- [你需要準備什麼](#prerequisites)
- [安裝](#install)
- [快速開始](#quick-start)
- [使用方式](#usage)
- [YouTube 擋下載時：PO Token Server](#po-token-server)
- [疑難排解](#troubleshooting)
- [已知限制](#known-limitations)
- [Roadmap / Future Work](#roadmap)
- [進階：架構與專案檔案](#advanced)
- [貢獻與回報問題](#contributing)
- [授權](#license)

---

<a id="what-is-this"></a>
## 這是什麼？

這是一套 **sentence mining（句子挖掘）** 工具：與其背單字表，不如從你真正看的影片裡收集「含生字的完整句子」來學——有語境、有聲音、有畫面來源，記憶效果遠比孤立單字好。

挑字遵循 **i+1 原則**（輸入內容只比現有程度難一點點）：每張卡的例句**只鎖定一個目標生字**——排除你 Anki 牌組裡已有的字後，整句剛好只含一個落在學習頻率帶內的新字，語境負擔最小。

適合對象：用 Anki 背單字、常看英文 YouTube（需有英文 CC 字幕）、想把「看過的影片」變成「複習素材」的中文使用者。

> 在 macOS（Apple Silicon）上開發與測試。一鍵腳本 `run.sh` 依賴 macOS 指令（如 `open -a Anki`）；核心 Python 管線理論上跨平台，但未在 Windows/Linux 驗證。

---

<a id="prerequisites"></a>
## 你需要準備什麼

**必需**（缺一不可）：

| 項目 | 說明 |
|---|---|
| [Anki 桌面版](https://apps.ankiweb.net)（免費） | 製卡與同步的核心，執行期間需保持開啟 |
| AnkiConnect add-on（免費） | 讓腳本能對 Anki 送卡，安裝見下方 |
| Python 3.10+ | 執行管線 |
| Git（macOS 通常已內建） | 下載本專案；不想裝也可從 GitHub 頁面「Code → Download ZIP」取得 |
| ffmpeg（`brew install ffmpeg`） | 從影片切出句子音檔 |

**建議**（沒有也能跑，但卡片品質差很多）：

| 項目 | 說明 |
|---|---|
| [Merriam-Webster API 金鑰](https://dictionaryapi.com)（免費） | 英英定義與同反義字的來源；沒設定則這兩欄留空 |

**條件式**（遇到才需要）：

| 項目 | 說明 |
|---|---|
| PO Token Server | YouTube 擋下載時的解法，見[專門章節](#po-token-server) |
| 手機版 Anki | 想在手機複習才需要：iPhone 用 [AnkiMobile](https://apps.apple.com/app/ankimobile-flashcards/id373493387)（付費）、Android 用 [AnkiDroid](https://play.google.com/store/apps/details?id=com.ichi2.anki)（免費），透過免費的 AnkiWeb 帳號同步 |

---

<a id="install"></a>
## 安裝

### 1. 安裝 AnkiConnect

在 Anki 裡：`Tools → Add-ons → Get Add-ons`，貼上代碼 **`2055492159`**，重啟 Anki。

預設設定（監聽 `127.0.0.1:8765`）即可直接使用，不需改 Config。驗證：
```bash
curl localhost:8765 -X POST -d '{"action":"version","version":6}'
```
預期回應：`{"result": 6, "error": null}`

### 2. 安裝本專案

```bash
git clone https://github.com/NightLightTw/youtube-anki-mining.git
cd youtube-anki-mining
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 3. 建立 deck 與 note type（Anki 需開著）

```bash
.venv/bin/python setup_anki.py
```
會建立 deck `YouTube Mining` 與 note type `YT Mining EN`（11 欄位 + 模板 + CSS），可重複執行；重跑會自動為既有 note type 補上新欄位。

> 沒有 AnkiConnect 也想先拿到 note type？跑 `.venv/bin/python make_apkg.py` 產出 `YT_Mining_EN.apkg` 匯入即可——但注意這只是 note type 備援，**自動製卡本身仍需要 AnkiConnect**。

### 4.（建議）設定 Merriam-Webster 金鑰

到 <https://dictionaryapi.com> 註冊（免費非商用、每把 key 每日 1000 次），申請兩把 key：**Learner's Dictionary** 與 **Collegiate Thesaurus**。在專案根目錄建 `.env`（已被 `.gitignore` 排除，勿進版控）：

```
MW_LEARNERS_KEY=你的-learners-key
MW_THESAURUS_KEY=你的-thesaurus-key
```

有 MW 定義時，繁中釋義也會以英文定義當語境提示去翻譯，比裸字直翻準確。

---

<a id="quick-start"></a>
## 快速開始

```bash
./run.sh "https://youtu.be/xxxxxxxxxxx"
```

就這樣。腳本依序：檢查環境 → 確認/啟動 PO Token Server（若已安裝）→ 解析網址 → 確認 Anki/AnkiConnect → 下載字幕與影片 → 全自動挑字製卡 → 同步 AnkiWeb。

**跑完你會得到**：

- 預設最多 **20 張**新卡，放進 Anki 的 **`YouTube Mining`** 牌組
- 每張卡含：目標字、例句（生字標色）、繁中釋義、句子發音 mp3、影片時間戳連結；有設 MW 金鑰時再加上英英定義與同反義字（沒設則這兩欄留空，屬正常現象）
- 卡片已推上 AnkiWeb；手機端開 Anki App 手動「同步」一次即可開始複習
- 任一步失敗會清楚指出中斷位置，已完成的下載/卡片不會遺失，修正後直接重跑同一指令即可

---

<a id="usage"></a>
## 使用方式

### 一鍵全自動（推薦）

```bash
./run.sh "https://youtu.be/xxxxxxxxxxx"
./run.sh "https://youtu.be/xxxxxxxxxxx" --max-cards 15   # 額外參數原封傳給 mine.py --auto
./run.sh "https://youtu.be/xxxxxxxxxxx" --with-image      # 附影片截圖（預設不留存）
```

### 自動挑字的邏輯

1. **已知字庫** — 從你現有 Anki 牌組撈所有單字（含已挖過的），這些不再做卡
2. **頻率過濾** — 用 `wordfreq` 的 Zipf 值，只留 `--min-zipf`（預設 2.5）到 `--max-zipf`（預設 4.2）之間的字：太常見＝已會、太罕見＝沒用
3. **i+1** — 只挑「整句剛好一個生字」的句子，確保語境好懂
4. 同一個字只留最短的一句；**進階字優先**（頻率帶內由難到易），取前 `--max-cards`（預設 20）個

```bash
# 先看會挑哪些字（不建卡）
.venv/bin/python mine.py --auto --dry-run -- "$ID"

# 確認後批次製卡
.venv/bin/python mine.py --auto --max-cards 15 --title "影片標題" -- "$ID"
```

> `$ID` 放最後、前面加 `--`：YouTube 影片 ID 偶爾以 `-` 開頭（如 `-JJ4OE0rZJo`），放在其他選項之前會被誤判成未知旗標而報錯；`--` 之後的內容一律當成位置參數。

調參方向：`--max-zipf` 調高 → 收更多較基礎的字；調低 → 只收進階字。`Word` 欄存原形（lemma），例句裡標色的是原始字形。

> 自動挑字品質不會比手動好——它靠頻率＋已知字庫猜，建完在 Anki 裡掃一遍、刪掉不要的即可。詳見「[已知限制](#known-limitations)」。

### 手動模式（自己挑句子/單字）

```bash
ID=iDG0rwm9GaQ   # 換成你的影片 ID

# 1) 下載英文字幕 + 低畫質影片（音檔來源）
.venv/bin/yt-dlp --write-subs --write-auto-subs --sub-lang "en.*" --sub-format srt --convert-subs srt \
  --skip-download -o "media/%(id)s.%(ext)s" "https://youtu.be/$ID"
# 若抓到的是 en-GB 等地區變體檔名，改名成 mine.py 預期的 .en.srt（run.sh 會自動做，這裡要手動）
[ -s "media/$ID.en.srt" ] || mv "$(ls "media/$ID".en*.srt | head -1)" "media/$ID.en.srt"
.venv/bin/yt-dlp -f "best[height<=360]" --merge-output-format mp4 \
  -o "media/%(id)s.%(ext)s" "https://youtu.be/$ID"

# 2) 列出候選句（索引 / 時間 / 字數）
.venv/bin/python mine.py --list -- "$ID"

# 3) 挑一句建卡
.venv/bin/python mine.py --index 28 --word indispensable \
  --collocation "be indispensable <b>to</b> sb/sth" \
  --title "影片標題" -- "$ID"
```

### 同步到手機

1. `run.sh` 跑完會自動把 Mac 端的卡推上 **AnkiWeb**（手動模式則自己按 Anki 右上角「同步」）
2. 手機端（AnkiMobile / AnkiDroid）開 App **手動同步一次**，`YouTube Mining` 牌組就會出現
3. 同步是兩段式、非即時：每挖一批，兩端各同步一次。音檔為 mp3，行動端可直接播放

---

<a id="po-token-server"></a>
## YouTube 擋下載時：PO Token Server

**先直接跑跑看**——部分影片不裝這個也能下載。但 2026 年中起 YouTube 大幅收緊反爬蟲，`yt-dlp` 很常遇到：

- `Sign in to confirm you're not a bot`
- `Requested format is not available`（畫質清單只剩 storyboard 縮圖）

遇到以上錯誤，就需要裝一個本機的 **PO Token 提供者**（[bgutil-ytdlp-pot-provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider)）幫 yt-dlp 生成驗證 token。**裝好一次**之後 `run.sh` 每次執行都會自動偵測並啟動它，不需再手動管理。

<details>
<summary><b>安裝步驟（點開展開）</b></summary>

前置需求：Node.js ≥ 20、git。

```bash
# 1) 下載並編譯 server
cd ~
git clone --single-branch --branch 1.3.1 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git
cd bgutil-ytdlp-pot-provider/server/
npm ci
npx tsc

# 2) 裝 yt-dlp 端的 plugin（在本專案的 venv 裡）
cd /path/to/youtube-anki-mining
.venv/bin/pip install -U bgutil-ytdlp-pot-provider
```

裝完後，`run.sh` 的「確認 PO Token Server」步驟會自動偵測 `~/bgutil-ytdlp-pot-provider/server/build`：存在就自動啟動（監聽 `127.0.0.1:4416`）、已在跑就直接沿用；找不到就只印警告、不中斷腳本。

</details>

> 這是 yt-dlp 社群方案，不保證長期有效——YouTube 與反爬蟲工具是持續拉鋸的攻防。未來若失效，到 [yt-dlp GitHub issues](https://github.com/yt-dlp/yt-dlp/issues) 搜尋最新對策。

---

<a id="troubleshooting"></a>
## 疑難排解

| 症狀 | 原因 / 解法 |
|---|---|
| `curl localhost:8765` 無回應 | Anki 沒開，或 AnkiConnect 沒裝。執行期間 Anki 必須開著。 |
| `yt-dlp` 出現 `Sign in to confirm you're not a bot` 或 `Requested format is not available` | YouTube 反爬蟲收緊。裝 [PO Token Server](#po-token-server)。 |
| 影片下載 `HTTP Error 403` | YouTube 暫時擋特定 player client。`run.sh` 會自動改用 android client 重試；重跑同一指令通常即可。 |
| 找不到英文字幕檔 | 該影片沒有英文字幕（人工或自動皆無），無法製卡。腳本已能處理 `en-GB` 等地區變體標籤。 |
| 定義/同義字留空 | `.env` 沒設 Merriam-Webster 金鑰，或當日超過 1000 次額度。 |
| 卡片跑進「預設」牌組 | 某些版本 AnkiConnect 的 `addNote` 忽略 `deckName`；`mine.py` 已用 `changeDeck` 處理。 |
| iPhone 上斷圖／斷音 | 媒體檔名大小寫不一致（iOS 區分大小寫）。`mine.py` 已把檔名一律小寫。 |
| 句子破碎/不完整 | 該影片只有自動字幕，品質有限；可換索引或用手動模式修句。 |

---

<a id="known-limitations"></a>
## 已知限制

目前的挑字與釋義管線是**純規則式**（頻率統計 + 詞形還原 + 詞性啟發式），沒有語意理解能力，因此有幾類已知的天花板：

1. **詞義消歧不完美** — 一個字在字典常有多個詞條/義項，管線靠詞性猜測＋內容重疊評分挑義項，遇到歧義結構或字典缺少該詞性詞條時會選錯，卡片會顯示與句意不符的定義。
2. **詞形還原有錯漏** — `simplemma` 會把部分詞形變化還原壞，目前靠覆寫表逐字修補。
3. **字典收錄有缺口** — Merriam-Webster 學習者字典查不到部分現代口語/慣用義（如 *dupe*=仿冒品、*chill*=放鬆的），英式拼法需備援轉換，少數字完全沒有詞條。
4. **繁中釋義偶爾對不上英文定義** — Google 翻譯即使有英文定義當語境提示，仍可能給出字面直翻或語域錯誤的翻譯。
5. **自動字幕品質限制** — 口吃斷詞、連字號黏字、拼寫數字已有過濾器擋掉常見型態，但無法窮舉；完全沒有標點的自動字幕無法斷句，目前不支援。

實務緩解：建卡後在 Anki 掃一遍，刪掉或修正不對的卡。系統性解法見下方 Roadmap 的 LLM 輔助方向。

---

<a id="roadmap"></a>
## 🗺 Roadmap / Future Work

### LLM 輔助挑字與釋義修正

上述限制多數本質上是**語意問題**，規則式管線再怎麼修補都有天花板；接一個 LLM 進管線可以系統性解決：

- **詞義消歧**：把整句 + 字典候選義項丟給 LLM 挑最符合句意的一個
- **挑字品質**：讓 LLM 判斷候選字在該句中是否值得學
- **釋義補洞**：字典查無時，讓 LLM 直接產生該句境下的英英定義 + 繁中釋義
- **中譯校對**：讓 LLM 核對翻譯結果與英文定義是否一致，不一致時重寫

實作上會設計成**選用層**（如 `--llm-assist` 旗標 + API 金鑰），沒設定時退回現行純規則管線，維持零額外成本可用。此流程目前由作者以 AI 協作方式人工執行（跑完管線後逐卡審查修正），已驗證有效，待內建進 `mine.py`。

### 互動逐句挖（asbplayer + Yomitan）

**未實作、未驗證**的另一條路線：邊看影片邊用 Yomitan 查詞、asbplayer 即時截取句子/音檔做卡，取代批次全自動模式。優點是即時互動、可自選定義來源；缺點是需裝瀏覽器擴充、逐句手動操作。`add_cors.py` 已寫好備用，其餘設定未實測；有興趣的人可參考 [`youtube-anki-mining-spec.md`](youtube-anki-mining-spec.md)。

---

<a id="advanced"></a>
## 進階：架構與專案檔案

<details>
<summary><b>管線架構（點開展開）</b></summary>

```
Python 管線 (yt-dlp + ffmpeg) → AnkiConnect(:8765) → Anki 桌面 → AnkiWeb → 手機 Anki App
```

- **句子重建** — YouTube 自動字幕是重疊的滾動片段，`mine.py` 串成完整句並回推起訖時間；有逐字時間戳（json3）時切點更精準
- **音檔／截圖** — ffmpeg 依句子時間切 mp3（行動端相容）＋取中點該幀截圖（選用）
- **定義 / 同反義字** — Merriam-Webster Learner's（學習者導向定義）與 Collegiate Thesaurus，需金鑰；沒金鑰則留空
- **中文釋義** — Google 翻譯（非官方端點，原生繁體 zh-TW；有 MW 英文定義時當語境提示）
- **送卡** — `storeMediaFile` + `addNote`，再 `changeDeck` 歸位

</details>

<details>
<summary><b>專案檔案導覽（點開展開）</b></summary>

| 檔案 | 用途 |
|---|---|
| `run.sh` | 一鍵流程：環境檢查 → PO Token Server → 下載 → 製卡 → 同步 |
| `anki.py` | AnkiConnect 呼叫工具 + note type 定義（欄位/模板/CSS 的單一事實來源） |
| `setup_anki.py` | 建立/更新 deck 與 note type（idempotent） |
| `mine.py` | 自動管線：字幕 → 句子重建 → ffmpeg → 送卡（含 `--auto` 全自動挑字）|
| `autopick.py` | 全自動挑字：已知字庫擷取 + 頻率過濾 + i+1 選字 |
| `sync_monkeytype.py` | 選配整合：把卡片單字庫與例句庫寫進相鄰的 monkeytype 打字練習專案，並改寫其短句白名單。`run.sh` 偵測到 `../monkeytype` 目錄存在時自動執行並修改該專案的檔案，否則靜默跳過，一般使用者不受影響 |
| `make_apkg.py` | 產出 `YT_Mining_EN.apkg`（note type 備援） |
| `add_cors.py` | 把瀏覽器擴充 origin 加進 AnkiConnect CORS 白名單（asbplayer 路線用） |
| `docs/` | README 素材 |
| `youtube-anki-mining-spec.md` | 原始設計規格 |

</details>

---

<a id="contributing"></a>
## 貢獻與回報問題

歡迎開 [Issue](https://github.com/NightLightTw/youtube-anki-mining/issues) 回報問題或提出想法。回報下載類問題時請附上：影片網址、錯誤訊息全文、`yt-dlp` 版本。

請理解：本工具依賴 YouTube（非官方存取）與第三方 API，上游隨時可能變動，不保證能立即修復所有失效情況。

<a id="license"></a>
## 授權

[MIT](LICENSE)
