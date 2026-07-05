"""半自動製卡管線（spec §9 進階選項）。

流程：SRT 字幕 → 句子重建(含時間軸) → ffmpeg 切句子 mp3 + 截圖
     → Merriam-Webster Learner's 取英英定義、Thesaurus 取同反義字
     → AnkiConnect 送卡到「YouTube Mining」。

用法：
  python mine.py <video_id> --list                 列出候選句子(含索引)
  python mine.py <video_id> --index N --word WORD   依索引建一張卡，目標字 WORD
"""
import argparse
import base64
import hashlib
import html
import json
import os
import re
import subprocess
import urllib.parse
import urllib.request

from anki import invoke, DECK_NAME, MODEL_NAME

MEDIA_DIR = "media"


# ---------- 設定（讀 .env 的 Merriam-Webster 金鑰）----------
def load_env(path=".env"):
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


load_env()
MW_LEARNERS_KEY = os.environ.get("MW_LEARNERS_KEY", "")
MW_THESAURUS_KEY = os.environ.get("MW_THESAURUS_KEY", "")
MW_BASE = "https://www.dictionaryapi.com/api/v3/references"


# ---------- 字幕解析 ----------
def parse_srt(path):
    content = open(path, encoding="utf-8").read()
    cues = []
    for block in re.split(r"\n\s*\n", content.strip()):
        m = re.search(
            r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", block
        )
        if not m:
            continue
        g = list(map(int, m.groups()))
        start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        lines = block.split("\n")
        ti = next(i for i, l in enumerate(lines) if "-->" in l)
        text = " ".join(lines[ti + 1:])
        text = re.sub(r">>", " ", text)          # 移除說話者標記
        text = re.sub(r"\[.*?\]", " ", text)      # 移除 [Music] 等
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            cues.append((start, end, text))
    return cues


def build_sentences(cues):
    """把連續字幕片段串成完整句子，回推每句起訖時間。

    YouTube 自動字幕只有「每段(cue)」時間、無每字時間，且滾動字幕段落重疊。
    若直接用「最後一字所在 cue 的結束時間」當句尾，會吃到下一句（聲音拖太長）。
    解法：在每段內依字數線性內插估計每個字的時間點。
      - 句首：用 cue 原始開始時間（實測 OK，不動）
      - 句尾：min(最後一字的內插結束, 下一個字的內插開始)
        → 不論句子結束在 cue 中間或邊界，都不會吃到下一句的聲音
    """
    words = []  # (word, cue_start, interp_start, interp_end)
    for s, e, t in cues:
        toks = t.split()
        n = len(toks)
        span = e - s
        for j, w in enumerate(toks):
            words.append((w, s, s + span * j / n, s + span * (j + 1) / n))
    sentences, cur = [], []
    for i, tup in enumerate(words):
        cur.append(i)
        if re.search(r'[.!?]["\')]?$', tup[0]):
            sentences.append(_mk_sentence(words, cur, i + 1))
            cur = []
    if cur:
        sentences.append(_mk_sentence(words, cur, len(words)))
    return sentences


def _mk_sentence(words, idxs, next_i):
    start = words[idxs[0]][1]                       # cue 原始開始（句首不動）
    interp_end = words[idxs[-1]][3]                 # 最後一字內插結束
    next_start = words[next_i][2] if next_i < len(words) else interp_end
    end = max(min(interp_end, next_start), start + 0.5)
    return {
        "text": " ".join(words[i][0] for i in idxs),
        "start": start,
        "end": end,
        "nwords": len(idxs),
    }


# ---------- 媒體 ----------
def run(cmd):
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except FileNotFoundError:
        raise RuntimeError(
            f"找不到執行檔 '{cmd[0]}'，請先安裝（ffmpeg：`brew install ffmpeg`）。"
        ) from None
    except subprocess.CalledProcessError as ex:
        stderr = (ex.stderr or b"").decode("utf-8", "replace")[-800:]
        raise RuntimeError(f"{cmd[0]} 失敗（exit {ex.returncode}）：\n{stderr}") from None


def extract_audio(video, start, end, out):
    dur = max(0.5, end - start)
    run(["ffmpeg", "-y", "-ss", f"{max(0,start-0.15):.3f}", "-i", video,
         "-t", f"{dur+0.3:.3f}", "-vn", "-acodec", "libmp3lame", "-q:a", "4", out])


def extract_image(video, t, out):
    run(["ffmpeg", "-y", "-ss", f"{t:.3f}", "-i", video,
         "-frames:v", "1", "-vf", "scale=480:-1", "-q:v", "4", out])


def _mw_get(ref, key, word):
    url = f"{MW_BASE}/{ref}/json/{urllib.parse.quote(word)}?key={key}"
    return json.load(urllib.request.urlopen(url, timeout=10))


def _mw_clean(text):
    """去掉 MW 的標記符號（{bc}、{it}...{/it}、{sx|...}、—often 等）。"""
    text = re.sub(r"\{bc\}", ": ", text)
    text = re.sub(r"\{/?it\}", "", text)
    text = re.sub(r"\{sx\|([^|}]*)\|*[^}]*\}", r"\1", text)
    text = re.sub(r"\{[^}]*\}", "", text)
    return html.escape(text.strip(" :"))


def fetch_definition(word):
    """Merriam-Webster Learner's Dictionary：乾淨、學習者導向的定義。"""
    if not MW_LEARNERS_KEY:
        return ""
    try:
        data = _mw_get("learners", MW_LEARNERS_KEY, word)
        out = []
        for entry in data:
            if not isinstance(entry, dict):
                continue  # 查無此字時 API 回傳拼字建議字串
            pos = entry.get("fl", "")
            for sd in entry.get("shortdef", [])[:1]:   # 只取最主要的 1 條，降低認知負荷
                out.append(f"<i>{html.escape(pos)}</i> {_mw_clean(sd)}")
            if out:
                break  # 只取第一個詞性條目，避免太雜
        return "<br>".join(out)
    except Exception as ex:
        print(f"  (定義查詢失敗：{ex})")
        return ""


def _google_translate(text):
    url = ("https://translate.googleapis.com/translate_a/single"
           "?client=gtx&sl=en&tl=zh-TW&dt=t&q=" + urllib.parse.quote(text))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    data = json.load(urllib.request.urlopen(req, timeout=10))
    return "".join(seg[0] for seg in data[0] if seg[0])


def fetch_chinese(word, definition_hint=""):
    """Google 翻譯（非官方端點）→ 原生繁體中文 zh-TW。

    單獨翻譯裸字很容易猜錯詞義（例：crust 裸翻會得到「地殼」而非「餅皮」），
    因為 Google 手上除了那個字什麼語境都沒有。若有 MW 英文定義可用，改翻譯
    「word: definition」讓它有語境判斷，再切出冒號前的譯文。
    翻譯偶爾會整段放棄翻譯（結果仍殘留英文字母），這種情況就退回裸字翻譯。
    """
    if definition_hint:
        plain_def = re.sub(r"<[^>]+>", "", definition_hint).split("<br>")[0]
        plain_def = re.sub(r"^\s*(noun|verb|adjective|adverb)\s+", "", plain_def,
                            flags=re.I).strip()
        if plain_def:
            try:
                combined = _google_translate(f"{word}: {plain_def}")
                for sep in ("：", ":"):
                    if sep in combined:
                        candidate = combined.split(sep, 1)[0].strip()
                        if candidate and not re.search(r"[A-Za-z]", candidate):
                            return html.escape(candidate)
                        break  # 翻譯失敗（殘留英文）→ 跳出，改走下面的裸字翻譯
            except Exception:
                pass  # 帶語境翻譯呼叫本身失敗（逾時等）→ 一併退回裸字翻譯
    try:
        return html.escape(_google_translate(word))
    except Exception as ex:
        print(f"  (中文翻譯失敗：{ex})")
        return ""


def fetch_synonyms(word, n_syn=6, n_ant=4):
    """Merriam-Webster Thesaurus：同反義字。"""
    if not MW_THESAURUS_KEY:
        return ""
    try:
        data = _mw_get("thesaurus", MW_THESAURUS_KEY, word)
        for entry in data:
            if not isinstance(entry, dict):
                continue
            meta = entry.get("meta", {})
            syns_lists = meta.get("syns") or []
            ants_lists = meta.get("ants") or []
            syns = syns_lists[0][:n_syn] if syns_lists else []
            ants = ants_lists[0][:n_ant] if ants_lists else []
            parts = []
            if syns:
                parts.append("≈ " + ", ".join(html.escape(s) for s in syns))
            if ants:
                parts.append('<span class="ant">≠ ' +
                             ", ".join(html.escape(a) for a in ants) + "</span>")
            if parts:
                return "<br>".join(parts)
        return ""
    except Exception as ex:
        print(f"  (同義字查詢失敗：{ex})")
        return ""


# ---------- 送卡 ----------
def store(path, filename):
    data = base64.b64encode(open(path, "rb").read()).decode()
    invoke("storeMediaFile", filename=filename, data=data)
    return filename


def highlight(sentence, word):
    """先 html.escape 句子，再把目標字（所有出現）包成 <b>。"""
    safe = html.escape(sentence)
    return re.sub(rf"\b({re.escape(html.escape(word))})\b", r"<b>\1</b>", safe,
                  flags=re.IGNORECASE)


def add_card(video_id, video_file, sent, word, title, collocation="", highlight_word=None):
    start = sent["start"]
    mid = (sent["start"] + sent["end"]) / 2
    # slug 全小寫（避免 Anki 媒體層大小寫正規化讓 iPhone 斷圖斷音）；
    # 加句子內容短雜湊，避免「同一 cue 含多句 → start 相同 → 檔名碰撞」。
    h = hashlib.md5(sent["text"].encode("utf-8")).hexdigest()[:8]
    slug = f"ytm_{video_id}_{int(start*1000)}_{h}".lower()
    audio_fn, img_fn = f"{slug}.mp3", f"{slug}.jpg"

    # 預檢是否重複（第一欄 Word）：若重複就不切媒體、不查 API，避免留下孤兒媒體
    probe = {"deckName": DECK_NAME, "modelName": MODEL_NAME,
             "fields": {"Word": html.escape(word)}, "options": {"allowDuplicate": False}}
    if not invoke("canAddNotes", notes=[probe])[0]:
        print(f"  ↷ 跳過（已存在）：{word}")
        return None

    extract_audio(video_file, sent["start"], sent["end"], f"{MEDIA_DIR}/{audio_fn}")
    extract_image(video_file, mid, f"{MEDIA_DIR}/{img_fn}")
    store(f"{MEDIA_DIR}/{audio_fn}", audio_fn)
    store(f"{MEDIA_DIR}/{img_fn}", img_fn)

    definition = fetch_definition(word)
    synonyms = fetch_synonyms(word)
    chinese = fetch_chinese(word, definition_hint=definition)
    url = f"https://youtu.be/{urllib.parse.quote(video_id)}?t={int(start)}"

    note = {
        "deckName": DECK_NAME,
        "modelName": MODEL_NAME,
        "fields": {
            "Word": html.escape(word),
            "Sentence": highlight(sent["text"], highlight_word or word),
            "Definition": definition,
            "Chinese": chinese,
            "Collocation": collocation,   # 刻意不 escape：允許使用者用 <b> 標搭配重點
            "Synonyms": synonyms,
            "SentenceAudio": f"[sound:{audio_fn}]",
            "WordAudio": "",
            "Image": f'<img src="{html.escape(img_fn, quote=True)}">',
            "Source": html.escape(title),
            "URL": f'<a href="{html.escape(url, quote=True)}">{html.escape(url)}</a>',
        },
        "tags": ["youtube-mining", video_id],
        "options": {"allowDuplicate": False},
    }
    note_id = invoke("addNote", note=note)
    # 這版 AnkiConnect 的 addNote 會忽略 deckName（卡落在「預設」），用 changeDeck 強制歸位
    card_ids = invoke("findCards", query=f"nid:{note_id}")
    invoke("changeDeck", cards=card_ids, deck=DECK_NAME)
    print(f"✓ 已建立 note {note_id}（卡 → {DECK_NAME}）")
    print(f"  Word: {word}")
    print(f"  Sentence: {note['fields']['Sentence']}")
    print(f"  Definition: {definition or '(空)'}")
    print(f"  Chinese: {chinese or '(無)'}")
    print(f"  Synonyms: {synonyms or '(無)'}")
    print(f"  Audio: {audio_fn} / Image: {img_fn}")
    print(f"  URL: {url}")
    return note_id


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video_id")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--index", type=int)
    ap.add_argument("--word")
    ap.add_argument("--title", default="")
    ap.add_argument("--collocation", default="",
                    help="搭配片語，刻意允許 HTML（如 'comply <b>with</b>'）")
    ap.add_argument("--min-words", type=int, default=6)
    ap.add_argument("--max-words", type=int, default=22)
    # 全自動挑字
    ap.add_argument("--auto", action="store_true", help="自動挑 i+1 生字批次製卡")
    ap.add_argument("--max-cards", type=int, default=20)
    ap.add_argument("--min-zipf", type=float, default=2.5)
    ap.add_argument("--max-zipf", type=float, default=4.2)
    ap.add_argument("--dry-run", action="store_true", help="只列出自動挑的字，不建卡")
    args = ap.parse_args()

    # 模式互斥：--list / --auto / 手動(--index+--word) 三者剛好擇一，不可混用
    manual = args.index is not None or args.word is not None
    active = sum([bool(args.list), bool(args.auto), bool(manual)])
    if active != 1:
        ap.error("請擇一模式：--list、--auto、或手動(--index 且 --word)，不可混用")
    if manual and (args.index is None or args.word is None):
        ap.error("手動模式需同時提供 --index 與 --word")
    if args.dry_run and not args.auto:
        ap.error("--dry-run 只能搭配 --auto")

    srt = f"{MEDIA_DIR}/{args.video_id}.en.srt"
    video = f"{MEDIA_DIR}/{args.video_id}.mp4"
    # preflight：字幕一定要有；只有「實際製卡」（--auto 非 dry-run，或手動模式）才需影片
    if not os.path.exists(srt):
        ap.error(f"找不到字幕檔：{srt}\n請先用 yt-dlp 下載（見 README 方式 A 步驟 1）")
    need_video = (args.auto and not args.dry_run) or manual
    if need_video and not os.path.exists(video):
        ap.error(f"找不到影片檔：{video}\n請先用 yt-dlp 下載 360p 影片（見 README）")

    cues = parse_srt(srt)
    sents = build_sentences(cues)
    if not sents:
        ap.error(f"字幕解析不到任何句子：{srt}")

    if args.list:
        for i, s in enumerate(sents):
            if args.min_words <= s["nwords"] <= args.max_words:
                print(f"[{i:3d}] {s['start']:6.1f}s ({s['nwords']}w) {s['text']}")
        return

    if args.auto:
        from autopick import build_known_set, auto_select
        print("撈取已知字庫中...")
        known = build_known_set()
        print(f"已知字 {len(known)} 個；分析句子中...")
        picks = auto_select(sents, known, args.min_words, args.max_words,
                            args.min_zipf, args.max_zipf)[:args.max_cards]
        print(f"自動挑出 {len(picks)} 個 i+1 生字\n")
        if args.dry_run:
            for c in picks:
                print(f"  [{c['lemma']:14s} z={c['zipf']:.1f}] {c['sent']['text']}")
            return
        ok = skipped = failed = 0
        for c in picks:
            try:
                nid = add_card(args.video_id, video, c["sent"], c["lemma"],
                               args.title, highlight_word=c["surface"])
                if nid is None:          # 重複被預檢跳過
                    skipped += 1
                else:
                    ok += 1
                print()
            except Exception as ex:
                failed += 1
                print(f"✗ 失敗 {c['lemma']}：{ex}\n")
        print(f"完成：建立 {ok} 張、跳過(重複) {skipped} 張、失敗 {failed} 張"
              f"（候選 {len(picks)}）")
        return

    if not (0 <= args.index < len(sents)):
        ap.error(f"--index {args.index} 超出範圍（0~{len(sents)-1}）；可先用 --list 看索引")
    sent = sents[args.index]
    add_card(args.video_id, video, sent, args.word, args.title, args.collocation)


if __name__ == "__main__":
    main()
