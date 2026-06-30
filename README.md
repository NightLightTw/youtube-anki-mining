# YouTube → Anki 英文單字挖掘系統

把 YouTube 英文影片的 CC 字幕，挖成高品質 **Anki 句子卡**（含目標單字、完整例句、英英定義、句子真人發音、影片截圖、可回看的時間戳連結），匯入專用牌組「YouTube Mining」，再同步到 iPhone（AnkiMobile）複習。

設計理念：**語境記憶 > 裸背單字**，理想句子符合 **i+1**（整句只有一個生字）。完整設計緣由見 [`youtube-anki-mining-spec.md`](youtube-anki-mining-spec.md)。

> 在 macOS（Apple Silicon）上開發與測試。Python 管線本身跨平台；瀏覽器互動流程在 Chromium 系瀏覽器皆可。

---

## 使用方式

| 方式 | 適合 | 狀態 |
|---|---|---|
| **A. 自動管線** (`mine.py`) | 貼影片連結自動做卡、批次量產 | ✅ **已打通並驗證** |
| **B. 互動逐句挖** (asbplayer + Yomitan) | 邊看邊挑、自己選英英定義 | ⚠️ **尚未設定/未驗證**（僅文件，需自行安裝擴充，見附錄） |

目前實際可用的是 **方式 A**。方式 B 是原始 spec 的核心做法，本專案尚未實際安裝瀏覽器擴充、未填 CORS 擴充 ID、未測試——下方步驟僅供日後自行設定參考。

---

## 架構

```
瀏覽器 (asbplayer + Yomitan)  ─┐
                               ├─HTTP→ AnkiConnect(:8765) → Anki 桌面 → AnkiWeb → iPhone AnkiMobile
Python 管線 (yt-dlp+ffmpeg)  ─┘        「YouTube Mining」/「YT Mining EN」
```

---

## 前置需求

- **Anki 桌面版** — <https://apps.ankiweb.net>（同步用，需註冊免費 AnkiWeb 帳號）
- **AnkiConnect** add-on — 安裝見下方
- **Python 3.10+**、**ffmpeg**（`brew install ffmpeg`）
- （方式 B）Chromium 系瀏覽器 + **asbplayer** + **Yomitan** 擴充
- （複習端）iPhone **AnkiMobile**（付費 App）

---

## 安裝與設定（從零開始）

### 1. 安裝 AnkiConnect
在 Anki 裡：`Tools → Add-ons → Get Add-ons`，貼上代碼 **`2055492159`**，重啟 Anki。

開啟其 Config（`Tools → Add-ons → AnkiConnect → Config`），確認包含：
```json
{
  "webBindAddress": "127.0.0.1",
  "webBindPort": 8765,
  "webCorsOriginList": ["http://localhost", "https://app.asbplayer.dev"]
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
沒填 key 也能跑，只是 `Definition` / `Synonyms` 會留空（可改用 Yomitan 補）。

---

## 方式 A：自動管線 `mine.py`

```bash
ID=iDG0rwm9GaQ   # 換成你的影片 ID

# 1) 下載英文字幕 + 低畫質影片（音檔/截圖來源）
.venv/bin/yt-dlp --write-auto-subs --sub-lang en --sub-format srt --convert-subs srt \
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

### 全自動挑字（`--auto`）
讓程式自己挑「你還不會、又值得學、且有好例句」的字，整批製卡：

```bash
# 先看會挑哪些字（不建卡）
.venv/bin/python mine.py $ID --auto --dry-run

# 確認後批次製卡
.venv/bin/python mine.py $ID --auto --max-cards 15 --title "影片標題"
```

挑字邏輯：
1. **已知字庫** — 從你現有 Anki 牌組撈所有單字（含已挖過的），這些不再做卡。
2. **頻率過濾** — 用 `wordfreq` 的 Zipf 值，只留 `--min-zipf`(預設 2.5) 到 `--max-zipf`(預設 4.2) 之間的字（太常見=已會、太罕見=沒用）。
3. **i+1** — 只挑「整句剛好一個生字」的句子，確保語境好懂。
4. 同一個字只留最短的一句；**進階字優先**（頻率帶內由難到易），取前 `--max-cards`(預設 20) 個。

調參：`--max-zipf` 調高 → 收更多較基礎的字；調低 → 只收進階字。`Word` 存原形(lemma)、例句裡標色的是原始字形。

> 自動挑字品質不會比手動好——它靠頻率＋已知字庫猜，建完在 Anki 裡掃一遍刪掉不要的即可。

運作方式：
- **句子重建** — YouTube 自動字幕是重疊的滾動片段，`mine.py` 串成完整句並回推起訖時間。
- **音檔／截圖** — ffmpeg 依句子時間切 mp3（iOS 相容）+ 取中點該幀截圖。
- **定義 / 同反義字** — 來自 Merriam-Webster Learner's（學習者導向定義）與 Collegiate Thesaurus（同義/反義字），需 §4 的金鑰；沒金鑰則留空。
- **中文釋義** — 來自 Google 翻譯（非官方端點，原生繁體 zh-TW，連單字片語都準）；免金鑰、需連網，失敗則留空。
- **送卡** — `storeMediaFile` + `addNote`，再 `changeDeck` 歸位。

---

## 附錄：方式 B 互動逐句挖（asbplayer + Yomitan）— ⚠️ 尚未打通

> **此方式目前未設定、未驗證。** 以下僅為日後自行設定的參考步驟；本專案沒有安裝過這些擴充，也沒測試過送卡流程。實際可用請走方式 A。

1. **裝擴充**：Chrome/Brave/Edge 裝 asbplayer 與 Yomitan（Safari 不支援）。
2. **加 CORS 白名單**：`chrome://extensions` 開「開發人員模式」記下兩擴充 ID，然後：
   ```bash
   .venv/bin/python add_cors.py chrome-extension://<asbplayer-id> chrome-extension://<yomitan-id>
   ```
   完全關閉並重開 Anki。
3. **asbplayer → Settings → Anki**，逐欄對應：

   | 設定項 | 值 |
   |---|---|
   | Deck | `YouTube Mining` |
   | Note type | `YT Mining EN` |
   | Sentence field | `Sentence` |
   | Definition field | `Definition` |
   | Word field | `Word` |
   | Audio field | `SentenceAudio` |
   | Image field | `Image` |
   | Source field | `Source` |
   | URL field | `URL` |
   | **Audio export format** | **`mp3`**（iOS 必須） |

   Mining 分頁勾 **Update last card**。
4. **Yomitan 字典**（Settings → Dictionaries → Import）：
   - 英英：社群「Umbrella」單語字典（MacMillan / Oxford / Cambridge / Longman）或 OALD
   - 英中：見下方資源連結
   - 頻率表：COCA / FLT，輔助判斷 i+1
   - Yomitan → Settings → Anki：URL `http://127.0.0.1:8765`，note type `YT Mining EN`，Word→Word、Definition→Definition。
5. **挖字**：YouTube 開 CC → Yomitan 查詞按 `+` 預填 Word/Definition → asbplayer `Ctrl+Shift+X` 開製卡對話補句子/截圖/音檔 → 匯出。

字典下載入口（連結會變動）：
- Yomitan 官方字典頁：<https://yomitan.wiki/dictionaries/>
- awesome-yomitan：<https://github.com/awesome-list-community/awesome-yomitan>

---

## 同步到 iPhone

1. Mac Anki 右上「同步」→ 推到 AnkiWeb。
2. iPhone AnkiMobile 下拉「同步集合」→「YouTube Mining」出現 → 複習。
3. **非即時**：每挖一批，兩端各手動同步一次。音檔須 mp3 才播得出。

---

## 專案檔案

| 檔案 | 用途 |
|---|---|
| `anki.py` | AnkiConnect 呼叫工具 + note type 定義（欄位/模板/CSS 的單一事實來源） |
| `setup_anki.py` | 建立/更新 deck 與 note type（idempotent） |
| `mine.py` | 自動管線：字幕 → 句子重建 → ffmpeg → 送卡（含 `--auto` 全自動挑字）|
| `autopick.py` | 全自動挑字：已知字庫擷取 + 頻率過濾 + i+1 選字 |
| `make_apkg.py` | 產出 `YT_Mining_EN.apkg` 備援 |
| `add_cors.py` | 把瀏覽器擴充 origin 加進 AnkiConnect CORS 白名單 |
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
| asbplayer/Yomitan 送卡失敗 | CORS 沒放行該擴充 ID，用 `add_cors.py` 加入後重開 Anki。 |
| 句子破碎/不完整 | 該影片只有自動字幕，品質有限；可換索引或手動修句。 |
