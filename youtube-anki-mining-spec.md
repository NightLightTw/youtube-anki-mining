# YouTube → Anki 英文單字挖掘系統 — 架構與建置 Spec

> 給 Claude Code 的執行規格書。目標：在 macOS (M5 MacBook Air) 上，把 YouTube 英文影片的 CC 字幕，挖成高品質 Anki 句子卡，匯入專用牌組「YouTube Mining」，最後同步到 iPhone (AnkiMobile) 複習。
>
> 設計原則：**用現成工具，不重造輪子**。所有元件皆為官方/開源套件，本 spec 只負責「組裝、設定、產出 note type」。

---

## 0. TL;DR — 系統一句話

> 瀏覽器 (asbplayer 擴充 + Yomitan) 偵測 YouTube CC 字幕 → 挖字時把單字/定義/例句/截圖/句子音檔 → 透過 AnkiConnect (localhost:8765) 寫入 Mac 端 Anki 的「YouTube Mining」牌組 → 手動同步到 AnkiWeb → iPhone 下拉同步後複習。

---

## 1. 架構圖

```
┌─────────────────────────── M5 MacBook Air (macOS) ───────────────────────────┐
│                                                                               │
│   ┌────────────────────────┐         ┌──────────────────────────────────┐    │
│   │  Chromium 系瀏覽器       │         │  Anki Desktop (桌面版，需開著)      │    │
│   │  (Chrome/Brave/Edge)    │         │                                  │    │
│   │                         │         │  ┌────────────────────────────┐  │    │
│   │  ┌──────────────────┐   │  HTTP   │  │ AnkiConnect (add-on)       │  │    │
│   │  │ asbplayer 擴充    │───┼─────────┼─▶│ listen 127.0.0.1:8765      │  │    │
│   │  │ (抓 CC 字幕/截圖/  │   │ :8765   │  │ webCorsOriginList 要放行   │  │    │
│   │  │  句子音檔/製卡對話)│   │         │  └────────────────────────────┘  │    │
│   │  └──────────────────┘   │         │                                  │    │
│   │  ┌──────────────────┐   │         │  Note Type: "YT Mining EN"       │    │
│   │  │ Yomitan 擴充      │   │         │  Deck: "YouTube Mining"          │    │
│   │  │ (英英/英中查詞,    │   │         │  音檔輸出: mp3 (iOS 相容)         │    │
│   │  │  + 鈕填 word/def) │   │         └──────────────┬───────────────────┘    │
│   │  └──────────────────┘   │                        │                        │
│   └─────────────────────────┘                        │ 手動同步                │
│                                                       ▼                        │
│                                              ┌─────────────────┐               │
│                                              │   AnkiWeb       │               │
└──────────────────────────────────────────────┴────────┬────────┴──────────────┘
                                                         │ 下拉同步
                                                         ▼
                                              ┌─────────────────┐
                                              │ iPhone AnkiMobile│  ← 複習在這
                                              └─────────────────┘
```

---

## 2. 元件清單 (全部現成)

| 元件 | 角色 | 取得方式 | 備註 |
|---|---|---|---|
| Anki Desktop | 卡片資料庫 + 同步中樞 | apps.ankiweb.net | Apple Silicon 原生版 |
| AnkiConnect | 對外 HTTP API (port 8765) | Anki 內 Tools→Add-ons→Get Add-ons，代碼 `2055492159` | 挖字工具靠它寫卡 |
| Chromium 瀏覽器 | 跑擴充 | Chrome / Brave / Edge | **Safari 不支援**，必須 Chromium 系 |
| asbplayer | 抓字幕/截圖/句子音檔、製卡 | Chrome Web Store | 自動偵測 YouTube CC |
| Yomitan | 滑鼠查詞、預填 word/definition | Chrome Web Store | 需匯入英文字典 |
| 英文字典檔 (Yomitan 格式) | 提供定義 | 見 §6 | 英英 + 英中各一 |
| AnkiMobile | iPhone 複習端 | App Store (付費) | 音檔須 mp3 |

---

## 3. Note Type 設計：`YT Mining EN`

### 3.1 欄位 (Fields)

| # | 欄位名 | 來源 | 說明 |
|---|---|---|---|
| 1 | `Word` | Yomitan 預填 | 目標單字，原形 (lemma) |
| 2 | `Sentence` | asbplayer 字幕 | 影片該句完整句子；目標字會被標色/挖空 |
| 3 | `Definition` | Yomitan 預填 | 英英為主，英中輔；可含詞性 |
| 4 | `Collocation` | 手動/留空 | TOEIC 取向：常見搭配、片語 (e.g. "comply **with**") |
| 5 | `SentenceAudio` | asbplayer | 句子真人發音 (mp3) |
| 6 | `WordAudio` | Yomitan/Forvo | 單字單獨發音 (mp3，選填) |
| 7 | `Image` | asbplayer | 影片該幀截圖 |
| 8 | `Source` | asbplayer | 影片標題 |
| 9 | `URL` | asbplayer | 影片連結 (含時間戳，可回看) |

> 設計依據：sentence mining 的核心是「語境記憶 > 裸背單字」，且理想句子符合 **i+1**（整句只有一個生字）。`Collocation` 是針對你 TOEIC 牌組加的，Part 5/6 大量考詞性與搭配。

### 3.2 卡片模板 (Card Template)

**正面 (Front)** — 先看挖空句子 + 句子音檔，強迫在語境中回想：

```html
<div class="sentence">{{cloze-or-highlight:Sentence}}</div>
<div class="audio">{{SentenceAudio}}</div>
```

> 註：asbplayer 不是 cloze note，所以「挖空」用 highlight 呈現（目標字標色），或在 Sentence 欄位用 `<b>` 包目標字。若要真正挖空，需改用 Cloze note type（見 §3.4 變體）。

**背面 (Back)**：

```html
{{FrontSide}}
<hr id=answer>
<div class="word">{{Word}}</div>
<div class="definition">{{Definition}}</div>
{{#Collocation}}<div class="colloc">🔗 {{Collocation}}</div>{{/Collocation}}
<div class="word-audio">{{WordAudio}}</div>
<div class="image">{{Image}}</div>
<div class="source">{{Source}} · {{URL}}</div>
```

### 3.3 樣式 (CSS)

```css
.card {
  font-family: -apple-system, "Helvetica Neue", sans-serif;
  font-size: 20px;
  text-align: center;
  color: #1a1a1a;
  background: #fafafa;
}
.sentence { font-size: 22px; line-height: 1.6; margin: 16px 0; }
.sentence b, .target { color: #2962ff; font-weight: 700; }
.word { font-size: 28px; font-weight: 700; margin-top: 12px; }
.definition { font-size: 18px; color: #333; margin: 10px 0; }
.colloc { font-size: 17px; color: #00796b; margin: 8px 0; }
.image img { max-width: 90%; border-radius: 8px; margin: 12px 0; }
.source { font-size: 13px; color: #999; margin-top: 16px; }
hr#answer { border: none; border-top: 1px solid #ddd; margin: 16px 0; }
@media (prefers-color-scheme: dark) {
  .card { color: #e0e0e0; background: #1e1e1e; }
  .definition { color: #ccc; }
}
```

### 3.4 變體：真正挖空 (選用)

若你偏好正面真正「___」挖空而非標色：改用 Cloze note type，`Sentence` 欄寫成 `{{c1::target word}}`。但 asbplayer 對 cloze 的自動填充支援較弱，需在製卡對話手動加 `{{c1::}}`。**建議先用 highlight 版，習慣後再評估。**

---

## 4. AnkiConnect 設定 (關鍵，否則送不進卡)

編輯 AnkiConnect config (Anki → Tools → Add-ons → AnkiConnect → Config)，確保 `webCorsOriginList` 放行 asbplayer：

```json
{
  "apiKey": null,
  "apiLogPath": null,
  "ignoreOriginList": [],
  "webBindAddress": "127.0.0.1",
  "webBindPort": 8765,
  "webCorsOriginList": [
    "http://localhost",
    "https://app.asbplayer.dev",
    "chrome-extension://"
  ]
}
```

> 從本機影片挖才嚴格需要 app.asbplayer.dev；從 YouTube 串流挖，瀏覽器擴充走的是 extension origin。兩個都放行最保險。改完**重啟 Anki**。

---

## 5. asbplayer 設定對應表

asbplayer Settings → Anki 分頁，逐欄填入 (對應 §3.1 的 note type)：

| asbplayer 設定項 | 值 |
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
| **Audio export format** | **`mp3`** ← iOS 必須 |

Mining 分頁：勾選 **Update last card**（讓 Yomitan 查詞後能補進同一張卡）。

---

## 6. Yomitan 英文字典

Yomitan 預設無英文字典，需自行匯入 (Settings → Dictionaries → Import)：

- **英英**：建議 Merriam-Webster / Oxford / Wiktionary (英文) 的 Yomitan 格式檔
- **英中**：CC-CEDICT 反查 或 Wiktionary 中文釋義檔
- **頻率表**：匯入英文頻率字典 (如 CEFR / COCA 頻率)，讓 Yomitan 顯示頻率，輔助判斷 i+1

Yomitan → Settings → Anki：URL 填 `http://127.0.0.1:8765`，note type 選 `YT Mining EN`，欄位對應同上 (Word→Word, Definition→Definition)。

> 字典檔來源 (社群整理) 需由 Claude Code 協助你在執行階段搜尋當下可用的下載點，連結會變動，不寫死在本 spec。

---

## 7. 同步到 iPhone 流程

1. Mac Anki 挖完卡 → 點主畫面右上「同步」→ 推到 AnkiWeb
2. iPhone AnkiMobile → 下拉「同步集合」(你截圖左下/右下那個)
3. 「YouTube Mining」牌組出現，開始複習

⚠️ **不是即時**。每次挖完一批，手動在 Mac 同步一次、iPhone 同步一次。音檔為 mp3 才能在手機播放。

---

## 8. 挖字操作流程 (日常使用)

1. Mac 開著 Anki (AnkiConnect 在跑)
2. 瀏覽器開 YouTube 影片，開啟 CC 字幕
3. asbplayer 自動偵測字幕（或手動綁定）
4. 看到要的字/句：
   - 滑鼠移到字上，Yomitan 查詞 → 點 `+` 預填 Word/Definition
   - 按 `Ctrl+Shift+X` 開 asbplayer 製卡對話，確認句子/截圖/音檔的時間區間 (可用滑桿微調)
   - 補 `Collocation` (選填)，匯出
5. 教學影片「後段口頭講解單字」段落：若 CC 有打出該字 → 照上面挖；若只有口說沒字幕 → 手動在 Yomitan 查該字製卡（句子欄可手打老師講的例句）
6. 一輪結束 → Mac 同步 → iPhone 同步 → 複習

---

## 9. Claude Code 任務清單 (建議執行順序)

> 以下是你在 Mac 上開 Claude Code 後，可請它逐步協助的工作。打勾項為 Claude Code 能直接代勞或產出的。

- [ ] **產生 `YT Mining EN.apkg`**：用 Python (genanki 套件) 依 §3 生成含欄位/模板/CSS 的空牌組 + note type，輸出 .apkg 供匯入。
- [ ] 檢查/修改 AnkiConnect config JSON (§4)，可用 AnkiConnect 的 API 直接寫入或產生正確 JSON 讓你貼上。
- [ ] 驗證 AnkiConnect 連線：`curl localhost:8765` 測 `version` / `deckNames` / `modelNames` action，確認 note type 與 deck 建立成功。
- [ ] 透過 AnkiConnect API 直接建立 deck `YouTube Mining`（免手動）。
- [ ] 搜尋當下可用的 Yomitan 英文字典 + 頻率表下載點 (§6)，連結易變動，執行時即時找。
- [ ] 產出一份「asbplayer 設定對照卡」(§5) 給你照填。
- [ ] (進階, 選用) 若想朝半自動：寫腳本用 `yt-dlp` 抓該影片字幕 → 切詞 → 比對你 Anki 已知字 → 依頻率列出新字清單 (類 Yomine 流程)，讓你批次挑選。

### genanki 產卡範例 (Claude Code 可直接跑)

```python
import genanki, random

model = genanki.Model(
    random.randrange(1 << 30, 1 << 31),
    'YT Mining EN',
    fields=[{'name': n} for n in
        ['Word','Sentence','Definition','Collocation',
         'SentenceAudio','WordAudio','Image','Source','URL']],
    templates=[{
        'name': 'Card 1',
        'qfmt': '<div class="sentence">{{Sentence}}</div>\n<div class="audio">{{SentenceAudio}}</div>',
        'afmt': '''{{FrontSide}}<hr id=answer>
<div class="word">{{Word}}</div>
<div class="definition">{{Definition}}</div>
{{#Collocation}}<div class="colloc">🔗 {{Collocation}}</div>{{/Collocation}}
<div class="word-audio">{{WordAudio}}</div>
<div class="image">{{Image}}</div>
<div class="source">{{Source}} · {{URL}}</div>''',
    }],
    css=open('style.css').read(),  # §3.3 的 CSS
)

deck = genanki.Deck(random.randrange(1 << 30, 1 << 31), 'YouTube Mining')
genanki.Package(deck).write_to_file('YT_Mining_EN.apkg')
```

---

## 10. 已知限制 / 注意事項

1. **純手機不可行**：AnkiConnect 只在桌面版跑，挖字一定要 Mac 開著。手機端只負責複習。
2. **非即時**：挖完 → 手動雙向同步才會出現在 iPhone。
3. **音檔務必 mp3**：否則 AnkiMobile / AnkiWeb 播不出。
4. **口說無字幕的單字抓不到**：asbplayer 靠字幕文字運作；老師純口頭講、字幕沒打的字，需手動補卡。
5. **不要污染共享牌組**：挖來的卡進獨立的「YouTube Mining」，勿混入別人分享的「NEW TOEIC」(模板欄位不同會錯位)。
6. **Safari 不支援**：擴充只在 Chromium 系瀏覽器。

---

## 11. 驗收標準 (Definition of Done)

- [ ] Mac Anki 有「YouTube Mining」牌組與「YT Mining EN」note type
- [ ] AnkiConnect `curl localhost:8765` 回得到 version
- [ ] 在 YouTube 開 CC，asbplayer 能抓到字幕、`Ctrl+Shift+X` 開得了製卡對話
- [ ] Yomitan 查英文字能 `+` 預填 Word/Definition
- [ ] 匯出一張測試卡，含句子、mp3 句子音檔、截圖、來源
- [ ] Mac 同步 → iPhone 同步後，該卡在 AnkiMobile 可複習、音檔播得出
