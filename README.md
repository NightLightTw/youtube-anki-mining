# YouTube → Anki 英文單字挖掘系統

把 YouTube 英文影片的 CC 字幕，全自動挖成高品質 **Anki 句子卡**：目標單字、完整例句（標色）、英英定義、繁體中文釋義、同反義字、句子真人發音、影片截圖、可回看的時間戳連結——貼一個網址，一鍵跑完下載、挑字、製卡、同步到 iPhone。

> 在 macOS（Apple Silicon）上開發與測試。Python 管線本身跨平台。

---

## ✨ Features

- **一鍵全自動**：`./run.sh "YouTube網址"` 跑完下載字幕/影片 → 挑生字 → 製卡 → 同步 AnkiWeb
- **i+1 語境挑字**：撈你現有 Anki 牌組排除已學單字 → 頻率過濾 → 只留「整句剛好一個生字」的乾淨例句 → 進階字優先，預設每支影片 20 張
- **卡片內容齊全**：例句標色、Merriam-Webster 英英定義／同反義字、Google 繁中釋義（設有 MW 金鑰時會帶英文定義當語境提示，比裸字直翻準；沒金鑰則退回裸字翻譯）、ffmpeg 切出的句子真人發音 mp3、影片截圖（預設不留存，需要時加 `--with-image` 開啟）、可回看的時間戳連結
- **穩健的工程細節**：跨平台媒體檔名一致性、自動去重、YouTube 反爬蟲（PO Token）自動偵測與啟動、AnkiConnect 錯誤友善提示
- **手動模式**：想自己挑句子/單字也支援 `--index` + `--word` 精準控制

---

## 🚀 Quick Start

```bash
# 1. Clone 專案並安裝 Python 依賴
git clone https://github.com/NightLightTw/youtube-anki-mining.git
cd youtube-anki-mining
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. 在 Anki 安裝 AnkiConnect（Tools → Add-ons → Get Add-ons → 代碼 2055492159），保持 Anki 開著

# 3. 建立 deck 與 note type
.venv/bin/python setup_anki.py

# 4. 貼一個 YouTube 網址，一鍵完成下載 + 製卡 + 同步
./run.sh "https://youtu.be/xxxxxxxxxxx"
```

> `ffmpeg` 需先裝好（`brew install ffmpeg`）。若 `yt-dlp` 被 YouTube 擋下載，見下方 [PO Token Server](#po-token-server) 章節。完整安裝細節、選用金鑰設定見「[安裝與設定](#安裝與設定從零開始)」。

---

## 架構

```
Python 管線 (yt-dlp + ffmpeg) → AnkiConnect(:8765) → Anki 桌面 → AnkiWeb → iPhone AnkiMobile
```

- **句子重建** — YouTube 自動字幕是重疊的滾動片段，`mine.py` 串成完整句並回推起訖時間
- **音檔／截圖** — ffmpeg 依句子時間切 mp3（iOS 相容）+ 取中點該幀截圖
- **定義 / 同反義字** — Merriam-Webster Learner's（學習者導向定義）與 Collegiate Thesaurus，需金鑰；沒金鑰則留空
- **中文釋義** — Google 翻譯（非官方端點，原生繁體 zh-TW；有 MW 英文定義可用時會當語境提示，比裸字直翻準確，沒有則直接裸字翻譯）
- **送卡** — `storeMediaFile` + `addNote`，再 `changeDeck` 歸位

---

## 前置需求

| 項目 | 用途 |
|---|---|
| **Anki 桌面版**（[下載](https://apps.ankiweb.net)） | 同步用，需註冊免費 AnkiWeb 帳號 |
| **AnkiConnect** add-on | 見下方安裝步驟 |
| **Python 3.10+** | 執行管線 |
| **ffmpeg**（`brew install ffmpeg`） | 切音檔/截圖 |
| iPhone **AnkiMobile**（付費 App） | 複習端 |

---

## 安裝與設定（從零開始）

### 1. 安裝 AnkiConnect
在 Anki 裡：`Tools → Add-ons → Get Add-ons`，貼上代碼 **`2055492159`**，重啟 Anki。

開啟其 Config（`Tools → Add-ons → AnkiConnect → Config`），確認包含：
```json
{
  "webBindAddress": "127.0.0.1",
  "webBindPort": 8765,
  "webCorsOriginList": ["http://localhost"]
}
```
改完重啟 Anki。驗證：
```bash
curl localhost:8765 -X POST -d '{"action":"version","version":6}'
# 預期：{"result": 6, "error": null}
```

### 2. 安裝 Python 依賴
```bash
cd youtube-anki-mining
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 3. 建立 deck 與 note type（Anki 需開著）
```bash
.venv/bin/python setup_anki.py
```
會建立 deck `YouTube Mining` 與 note type `YT Mining EN`（11 欄位 + 模板 + CSS），可重複執行。重跑也會自動為既有 note type 補上新欄位。

> 沒有 AnkiConnect 也想要 note type？跑 `.venv/bin/python make_apkg.py` 產出 `YT_Mining_EN.apkg`，在 Anki 直接匯入即可。

### 4.（選用，建議）Merriam-Webster 定義金鑰
自動管線的英英定義與同反義字來自 Merriam-Webster API（比免費 Wiktionary 乾淨太多）。免費非商用、每把 key 每日 1000 次：
1. 到 <https://dictionaryapi.com> 註冊，申請兩把 key：**Learner's Dictionary** 與 **Collegiate Thesaurus**。
2. 在專案根目錄建 `.env`（已被 `.gitignore` 排除，勿進版控）：
   ```
   MW_LEARNERS_KEY=你的-learners-key
   MW_THESAURUS_KEY=你的-thesaurus-key
   ```
沒填 key 也能跑，只是 `Definition` / `Synonyms` 會留空。

<a id="po-token-server"></a>
### 5.（YouTube 常擋下載時必裝）PO Token Server
2026 年中起 YouTube 大幅收緊反爬蟲機制，`yt-dlp` 常遇到兩種錯誤：
- `Sign in to confirm you're not a bot`
- `Requested format is not available`（畫質清單只剩 storyboard 縮圖）

解法是幫 yt-dlp 裝一個本機的 **PO Token 提供者**（[bgutil-ytdlp-pot-provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider)），跑一個小型 Node.js 服務幫忙生成驗證 token。`run.sh` 每次執行都會自動偵測並啟動它，你只需要**裝好一次**：

```bash
# 前置需求：Node.js >= 20、git（macOS 通常已內建 git）
cd ~
git clone --single-branch --branch 1.3.1 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git
cd bgutil-ytdlp-pot-provider/server/
npm ci
npx tsc

# 裝 yt-dlp 端的 plugin（在本專案的 venv 裡）
cd /path/to/youtube-anki-mining
.venv/bin/pip install -U bgutil-ytdlp-pot-provider
```

裝完之後，`run.sh` 執行時的「確認 PO Token Server」步驟會自動偵測 `~/bgutil-ytdlp-pot-provider/server/build` 是否存在：存在就自動啟動（監聽 `127.0.0.1:4416`）、已在跑就直接沿用；找不到就只印警告、不中斷腳本（部分影片沒有這個也能下載）。

> 這是 yt-dlp 社群方案，不保證長期有效——YouTube 與反爬蟲工具是持續拉鋸的攻防，未來若又失效，到 [yt-dlp GitHub issues](https://github.com/yt-dlp/yt-dlp/issues) 搜尋最新對策即可。

---

## 使用方式

### 一鍵全自動（推薦）
```bash
./run.sh "https://youtu.be/xxxxxxxxxxx"
./run.sh "https://youtu.be/xxxxxxxxxxx" --max-cards 15   # 額外參數原封傳給 mine.py --auto
./run.sh "https://youtu.be/xxxxxxxxxxx" --with-image      # 開啟截圖（預設不留存，需要視覺輔助時才加這個旗標）
```
依序執行：檢查環境 → 確認/啟動 PO Token Server → 解析網址 → 確認 Anki/AnkiConnect → 下載字幕與影片 → 全自動製卡 → 同步 AnkiWeb。任一步失敗會清楚指出中斷位置，已完成的下載/卡片不會遺失，修正後可直接重跑同一指令。

### 全自動挑字邏輯（`mine.py --auto`）
1. **已知字庫** — 從你現有 Anki 牌組撈所有單字（含已挖過的），這些不再做卡。
2. **頻率過濾** — 用 `wordfreq` 的 Zipf 值，只留 `--min-zipf`(預設 2.5) 到 `--max-zipf`(預設 4.2) 之間的字（太常見=已會、太罕見=沒用）。
3. **i+1** — 只挑「整句剛好一個生字」的句子，確保語境好懂。
4. 同一個字只留最短的一句；**進階字優先**（頻率帶內由難到易），取前 `--max-cards`(預設 20) 個。

```bash
# 先看會挑哪些字（不建卡）
.venv/bin/python mine.py $ID --auto --dry-run

# 確認後批次製卡
.venv/bin/python mine.py $ID --auto --max-cards 15 --title "影片標題"
```

調參：`--max-zipf` 調高 → 收更多較基礎的字；調低 → 只收進階字。`Word` 存原形(lemma)、例句裡標色的是原始字形。

> 自動挑字品質不會比手動好——它靠頻率＋已知字庫猜，建完在 Anki 裡掃一遍刪掉不要的即可。

### 手動模式（自己挑句子/單字）
```bash
ID=iDG0rwm9GaQ   # 換成你的影片 ID

# 1) 下載英文字幕 + 低畫質影片（音檔/截圖來源）
.venv/bin/yt-dlp --write-subs --write-auto-subs --sub-lang en --sub-format srt --convert-subs srt \
  --skip-download -o "media/%(id)s.%(ext)s" "https://youtu.be/$ID"
.venv/bin/yt-dlp -f "best[height<=360]" --merge-output-format mp4 \
  -o "media/%(id)s.%(ext)s" "https://youtu.be/$ID"

# 2) 列出候選句（索引 / 時間 / 字數）
.venv/bin/python mine.py $ID --list

# 3) 挑一句建卡
.venv/bin/python mine.py $ID --index 28 --word indispensable \
  --collocation "be indispensable <b>to</b> sb/sth" \
  --title "影片標題"
```

### 同步到 iPhone
1. Mac Anki 右上「同步」→ 推到 AnkiWeb（`run.sh` 會自動觸發，手動模式需自己按）。
2. iPhone AnkiMobile 下拉「同步集合」→「YouTube Mining」出現 → 複習。
3. **非即時**：每挖一批，兩端各手動同步一次。音檔須 mp3 才播得出。

---

## 專案檔案

| 檔案 | 用途 |
|---|---|
| `run.sh` | 一鍵流程：環境檢查 → PO Token Server → 下載 → 製卡 → 同步 |
| `anki.py` | AnkiConnect 呼叫工具 + note type 定義（欄位/模板/CSS 的單一事實來源） |
| `setup_anki.py` | 建立/更新 deck 與 note type（idempotent） |
| `mine.py` | 自動管線：字幕 → 句子重建 → ffmpeg → 送卡（含 `--auto` 全自動挑字）|
| `autopick.py` | 全自動挑字：已知字庫擷取 + 頻率過濾 + i+1 選字 |
| `make_apkg.py` | 產出 `YT_Mining_EN.apkg` 備援 |
| `add_cors.py` | 把瀏覽器擴充 origin 加進 AnkiConnect CORS 白名單（Future Work 用） |
| `requirements.txt` | Python 依賴 |
| `youtube-anki-mining-spec.md` | 原始設計規格 |

---

## 疑難排解

| 症狀 | 原因 / 解法 |
|---|---|
| `curl localhost:8765` 無回應 | Anki 沒開，或 AnkiConnect 沒裝。Anki 必須開著。 |
| 卡片跑進「預設」牌組 | 某些版本 AnkiConnect 的 `addNote` 忽略 `deckName`；`mine.py` 已用 `changeDeck` 處理。手動送卡時亦同。 |
| iPhone 上斷圖／斷音 | 媒體檔名大小寫不一致（iOS 區分大小寫）。`mine.py` 已把檔名一律小寫。 |
| 定義/同義字留空 | `.env` 沒設 Merriam-Webster 金鑰，或當日超過 1000 次額度。 |
| `yt-dlp` 出現 `Sign in to confirm you're not a bot` 或 `Requested format is not available` | YouTube 反爬蟲收緊。裝 [PO Token Server](#po-token-server)（見「安裝與設定」步驟 5），`run.sh` 會自動偵測並啟動它。 |
| 句子破碎/不完整 | 該影片只有自動字幕，品質有限；可換索引或手動修句。 |

---

## 🗺 Roadmap / Future Work

### 互動逐句挖（asbplayer + Yomitan）
原始 spec 的另一條路線：邊看影片邊用 Yomitan 查詞、asbplayer 即時截取句子/音檔做卡，取代目前「先下載整支影片再批次挑字」的全自動模式。優點是能自己選英英定義來源、即時互動；缺點是需要安裝瀏覽器擴充、逐句手動操作。

**目前狀態：未實作、未驗證。** `add_cors.py`（把擴充 origin 加進 AnkiConnect CORS 白名單）已寫好備用，但 asbplayer/Yomitan 的欄位對應、字典匯入等步驟尚未實測。主安裝流程的 AnkiConnect CORS 設定已精簡成只留全自動管線需要的 `http://localhost`；這個 repo 目前以全自動管線為主力，若之後有需求會回來補完：
- 安裝 asbplayer + Yomitan（Chrome/Brave/Edge）
- 用 `add_cors.py` 把 `https://app.asbplayer.dev` 等擴充 origin 加回 AnkiConnect CORS 白名單
- asbplayer 設定對應到 `YT Mining EN` 的欄位、Yomitan 匯入英英/英中字典
- 實測送卡流程、補上 CORS 擴充 ID 與截圖示範

有興趣搶先設定的人可參考 [`youtube-anki-mining-spec.md`](youtube-anki-mining-spec.md) 與 `add_cors.py` 的原始碼。
