"""半自動製卡管線（spec §9 進階選項）。

流程：SRT 字幕 → 句子重建(含時間軸) → ffmpeg 切句子 mp3 + 截圖
     → Merriam-Webster Learner's 取英英定義、Thesaurus 取同反義字
     → AnkiConnect 送卡到「YouTube Mining」。

用法：
  python mine.py <video_id> --list                 列出候選句子(含索引)
  python mine.py <video_id> --index N --word WORD   依索引建一張卡，目標字 WORD
"""
import argparse
import collections
import base64
import hashlib
import html
import json
import math
import os
import re
import subprocess
import time
import urllib.error
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
# 字幕裡代表現場聲響而非說話內容的標記，例如 (Laughter)、(Applause)、(Video starts)。
# 這類標記會被當成句子的一部分寫進例句，但音檔裡沒有這段聲音，卡片就變成
# 「字幕有、但聽不到」。實測一支 TED 影片有 23 處。
#
# 判斷方式是「括號裡有沒有出現聲響或動作詞」，不是看拼字型態。早期版本用
# 「大寫開頭的短句」當規則，會把 (New York)、(John Doe) 這種人名地名一起刪掉——
# 那是正文的一部分。詞彙比對沒有這個問題，也順帶能處理小寫寫法（(laughter)），
# W3C 的轉錄指引其實建議非語音標記用小寫。
_NONSPEECH_CUES = (
    r"laugh\w*|chuckl\w*|giggl\w*|applaus\w*|clap\w*|cheer\w*|boo|boos|boos\w+|"
    r"music|sing\w*|hum{1,2}\w*|whistl\w*|"
    r"sigh\w*|gasp\w*|groan\w*|moan\w*|sob\w*|cry\w*|cries|weep\w*|scream\w*|"
    r"shout\w*|yell\w*|whisper\w*|mumbl\w*|stammer\w*|"
    r"cough\w*|sneez\w*|snap\w*|clear\w*|swallow\w*|exhal\w*|inhal\w*|breath\w*|"
    r"imitat\w*|mimic\w*|impersonat\w*|"
    r"video|audio|recording|record\w*|beep\w*|noise|silence|pause|crack\w*|thud|bang|"
    r"inaudible|unintelligible|crosstalk|audience|crowd|footsteps?|knock\w*"
)
_NONSPEECH = re.compile(rf"\(\s*[^)]{{0,50}}?\b(?:{_NONSPEECH_CUES})\b[^)]{{0,50}}?\)",
                        re.IGNORECASE)


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
        # 現場聲響標記（見 _NONSPEECH 的說明）。只清括號裡含聲響／動作詞的，
        # 一般括號內容（補充說明、人名地名）保留。整個 cue 只有標記時，下面的
        # `if text:` 會把這個 cue 丟掉——它本來就沒有可學的句子。
        text = _NONSPEECH.sub(" ", text)
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            cues.append((start, end, text))
    return cues


# ---------- json3 逐字時間戳（YouTube 自動字幕限定）----------
# SRT 只給「整段(cue)」時間，句子邊界只能靠線性內插猜，誤差常達 ±0.6 秒以上。
# 但 YouTube 自動字幕的 json3 格式每個字都自帶 tOffsetMs，是 ASR 的實際對齊結果，
# 拿來當時間來源就不必猜。人工上傳的字幕沒有這個欄位（實測 BBC 影片為 0 個）。
MAX_WORD_DUR = 0.8   # 單一英文字的合理發音上限（秒），用來截斷句尾字被灌入整段停頓


def parse_json3(path):
    """解析 json3 自動字幕，回傳 [(字, 起, 迄), ...]（秒）。無逐字資訊則回傳 []。"""
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception:
        return []
    raw = []
    for ev in data.get("events", []):
        segs = ev.get("segs")
        if not segs:
            continue
        base = ev.get("tStartMs", 0)
        for sg in segs:
            txt = (sg.get("utf8") or "").strip()
            if not txt:
                continue
            raw.append((txt, (base + sg.get("tOffsetMs", 0)) / 1000.0))
    if not raw:
        return []
    raw.sort(key=lambda x: x[1])
    out = []
    for i, (w, st) in enumerate(raw):
        nxt = raw[i + 1][1] if i + 1 < len(raw) else st + MAX_WORD_DUR
        # 不能直接把「下一個字的開始」當結束：句子的最後一個字，下一個字是「下一句的
        # 第一個字」，整段句間停頓會被算進這個字裡，切下去剛好落在下一句的起音上
        # （實測讓結尾被硬切的比例不減反增）。用一般單字的合理上限截斷，
        # 剩下的交給 snap_boundaries 依真實音訊微調。
        en = min(nxt, st + MAX_WORD_DUR)
        out.append((w, st, max(en, st + 0.05)))
    return out


def _norm_word(w):
    return re.sub(r"[^a-z0-9']", "", w.lower())


def locate_sentence(words, sentence, hint_start, search=15.0, min_score=0.6):
    """在 json3 逐字流中定位一個句子，回傳 (score, start, end)；找不到回傳 None。

    卡片句子來自 SRT（可能是人工字幕，用字與 ASR 稍有出入），所以用「正規化後
    逐字比對的命中率」做模糊比對，而非要求完全相同。用 SRT 的粗略時間先把搜尋
    範圍縮到 ±search 秒，既快又避免比對到影片別處的相同句子。
    """
    target = [t for t in (_norm_word(w) for w in sentence.split()) if t]
    if not target or not words:
        return None
    best = None
    n = len(target)
    for i, (_, st, _e) in enumerate(words):
        if abs(st - hint_start) > search:
            continue
        window = words[i:i + n]
        if len(window) < n:
            break
        got = [_norm_word(w) for w, _, _ in window]
        score = sum(1 for a, b in zip(target, got) if a == b) / n
        if best is None or score > best[0]:
            best = (score, window[0][1], window[-1][2])
    if best and best[0] >= min_score:
        return best
    return None


def refine_with_json3(sents, json3_path):
    """有 json3 逐字時間戳就用它覆寫句子的起訖時間，回傳成功校正的句數。

    SRT 只能線性內插推算句子邊界，實測誤差最大到 2.6 秒；json3 的逐字時間是
    YouTube ASR 的實際對齊結果，拿來當時間來源可大幅縮小誤差，後面的靜音吸附
    才有辦法在合理視窗內找到真正的停頓。比對不到的句子維持原本的內插值。
    """
    if not os.path.exists(json3_path):
        return 0
    words = parse_json3(json3_path)
    if not words:
        return 0
    n = 0
    for s in sents:
        r = locate_sentence(words, s["text"], s["start"])
        if r:
            _score, st, en = r
            if en - st >= 0.4:          # 防呆：比對到不合理的極短區間就不採用
                s["start"], s["end"], s["from_json3"] = st, en, True
                n += 1
    return n


def build_sentences(cues):
    """把連續字幕片段串成完整句子，回推每句起訖時間。

    YouTube 自動字幕只有「每段(cue)」時間、無每字時間，且滾動字幕段落重疊，
    一個 cue 常塞好幾句話。若直接用「最後一字所在 cue 的結束時間」當句尾，
    會吃到下一句（聲音拖太長）。
    解法：在每段內依字數線性內插估計每個字的時間點。
      - 句首：用該字的內插起點（不是整個 cue 的起點——句子若不是 cue 裡第一句，
        用 cue 起點會把前面句子的聲音也一起切進來；句子恰為 cue 第一句時
        內插起點等於 cue 起點，行為不變）
      - 句尾：min(最後一字的內插結束, 下一個字的內插開始) 再加一點緩衝，
        線性內插假設每字等時長，遇到停頓/短促反應（如 "Wow."）會讓句尾估計
        偏早，緩衝可以降低把最後一個字尾音切掉的風險
        → 不論句子結束在 cue 中間或邊界，都不會吃到下一句太多聲音
    """
    words = []  # (word, cue_start, interp_start, interp_end)
    for i, (s, e, t) in enumerate(cues):
        toks = t.split()
        n = len(toks)
        # 內插的時間跨度不能直接用 cue 自己的長度：YouTube 滾動字幕的 cue 會互相
        # 重疊（每行留在畫面上直到下下行出現），所以 cue 的結束時間是「這行從畫面
        # 消失」而不是「這行話講完」。實測有 cue 標稱 6.4 秒、實際 1.9 秒就講完，
        # 用整段長度內插會把後面的字往後推到三秒之外。
        # 下一個 cue 開始時，語音通常已經進到那一行，所以用它當實際結束點；
        # 不重疊的一般字幕 next_start >= e，取 min 後等同原本行為，不受影響。
        # 這個假設不是 SRT 格式的保證（雙人重疊說話、字幕預先顯示等情況下，前一行
        # 的語音可能延續到下一個 cue 開始之後，那時這行的尾字會被壓短）。實測本專案
        # 全部 21424 個 cue 都沒有時間非遞增的情況，且改用此法後句首估計誤差大幅下降，
        # 因此接受這個取捨；若日後遇到多人對話素材切點變差，這裡是第一個要查的地方。
        eff_end = min(e, cues[i + 1][0]) if i + 1 < len(cues) else e
        span = max(0.0, eff_end - s)
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


_END_PAD = 0.3  # 句尾緩衝秒數，抵銷線性內插在停頓後容易估太早的偏誤


def _mk_sentence(words, idxs, next_i):
    start = words[idxs[0]][2]                       # 該字的內插起點（非整個 cue 的起點）
    interp_end = words[idxs[-1]][3]                 # 最後一字內插結束
    next_start = words[next_i][2] if next_i < len(words) else interp_end
    raw_end = min(interp_end, next_start)
    end = max(raw_end + _END_PAD, start + 0.5)
    return {
        "text": " ".join(words[i][0] for i in idxs),
        "start": start,
        "end": end,
        "nwords": len(idxs),
        # 時間目前來自 SRT 線性內插；refine_with_json3 校正成功會翻成 True。
        # 切音檔時要靠這個決定還需不需要靜音吸附（見 extract_audio 的呼叫端）。
        "from_json3": False,
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


# ---- 靜音吸附：把字幕推算出的邊界校正到音訊裡真實的停頓 ----
# 字幕只有「整段(cue)」時間，逐字位置是線性內插「猜」的，必然有 ±0.5 秒級誤差
# （實測全庫 80% 的音檔結尾落在語音正中間，聽起來被硬切掉）。固定緩衝是在錯誤
# 估計上再貼補丁：補太少還是切掉、補太多就吃到下一句。改成不信任估計值，切之前
# 先偵測真實音訊，把邊界吸附到最接近的真實停頓上。
SNAP_WINDOW = 1.2      # 邊界最多允許移動的秒數（超過就不信任，維持原估計）
                       # 實測：0.6 秒太窄，無逐字時間戳的影片有 49% 吸不到真實停頓；
                       # 放寬到 1.2 秒降到 29%，再寬則截斷句子的風險上升得比效益快
MIN_DUR_RATIO = 0.75   # 吸附後長度相對原估計的下限（防止吸到句中停頓把句子截斷）
MAX_DUR_RATIO = 1.50   # 上限（防止吸過頭把下一句包進來）
SNAP_MIN_SIL = 0.15    # 只認這麼長以上的靜音；更短的多半是字間停頓，不是句子邊界
SNAP_HEAD_PAD = 0.05   # 吸附後句首留一點餘裕，避免第一個音被削掉
SNAP_TAIL_PAD = 0.18   # 吸附後句尾留一點自然殘響，避免收得太乾
                       # （句間停頓通常 0.2~0.6s，留 0.18s 聽感自然又不會吃到下一句）


# 「靜音」的門檻不能固定：素材的收音與後製差異很大，實測 BBC podcast 有背景配樂
# 墊底，整段平均音量到 -17.6dB，用固定的 -35dB 門檻連一個停頓都找不到（但 -25dB
# 就能找到 3 個）。改成依該段自己的平均音量取相對門檻，讓有環境音的素材也能吸附。
SNAP_NOISE_BELOW_MEAN = 10.0   # 門檻 = 該段平均音量 - 這個值
SNAP_NOISE_FLOOR = -45.0       # 門檻下限（太低會退化成偵測不到停頓）
SNAP_NOISE_CEIL = -20.0        # 門檻上限（太高會把字與字之間的氣口也當成停頓）


def _adaptive_noise_db(video, win_start, win_dur, default=-35.0):
    """依該時間窗的實際平均音量決定靜音門檻。量測失敗則用預設值。"""
    p = subprocess.run(
        ["ffmpeg", "-ss", f"{win_start:.3f}", "-t", f"{win_dur:.3f}", "-i", video,
         "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    m = re.search(r"mean_volume:\s*([\-\d.]+) dB", p.stderr)
    if not m:
        return default
    thresh = float(m.group(1)) - SNAP_NOISE_BELOW_MEAN
    return max(SNAP_NOISE_FLOOR, min(SNAP_NOISE_CEIL, thresh))


def _detect_silences(video, win_start, win_dur, noise_db=None, min_sil=SNAP_MIN_SIL):
    """回傳指定時間窗內的靜音區間 [(絕對起, 絕對迄), ...]。"""
    if noise_db is None:
        noise_db = _adaptive_noise_db(video, win_start, win_dur)
    p = subprocess.run(
        ["ffmpeg", "-ss", f"{win_start:.3f}", "-t", f"{win_dur:.3f}", "-i", video,
         "-af", f"silencedetect=noise={noise_db}dB:d={min_sil}", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    starts = [float(x) for x in re.findall(r"silence_start:\s*([\d.-]+)", p.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end:\s*([\d.-]+)", p.stderr)]
    sils = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else win_dur
        sils.append((win_start + max(0.0, s), win_start + min(win_dur, e)))
    return sils


def snap_boundaries(video, start, end, window=SNAP_WINDOW):
    """把 (start, end) 吸附到音訊中最接近的真實停頓，回傳校正後的 (start, end)。

    句首吸附到「靜音結束點」(= 語音開始處)、句尾吸附到「靜音開始點」(= 語音結束處)，
    兩邊都取「離原估計最近」的那個，所以估計偏早會往後拉、偏晚會往前收，兩個方向的
    誤差都能修正——這正是固定緩衝做不到的地方（固定緩衝只能單向補償）。
    找不到可用停頓（例如整段連續語音）就原樣返回，不強行修改。
    """
    win_s = max(0.0, start - window)
    win_e = end + window
    try:
        sils = _detect_silences(video, win_s, win_e - win_s)
    except Exception:
        return start, end          # 偵測失敗不影響製卡，退回原估計
    if not sils:
        return start, end

    cand_start = [e for _, e in sils if abs(e - start) <= window]
    new_start = min(cand_start, key=lambda t: abs(t - start)) if cand_start else start

    # 句尾候選要先過濾長度合理性：視窗放寬後，最近的停頓有可能是「句子中間」的停頓，
    # 吸過去會把句子後半截掉（實測 2 秒視窗有 12% 的卡片長度被砍掉四分之一以上）。
    # 估計的「長度」比估計的「絕對位置」可靠得多（它來自字數），拿它當合理性下限。
    est_dur = end - start
    cand_end = [s for s, _ in sils if abs(s - end) <= window]
    plausible = [c for c in cand_end
                 if MIN_DUR_RATIO * est_dur <= (c - new_start) <= MAX_DUR_RATIO * est_dur]
    # 沒有長度合理的候選時就不吸附句尾，維持原估計：吸到不合理的點會把句子講到
    # 一半的內容切掉，而「收尾稍微突兀」遠比「少聽到幾個字」輕微。
    new_end = min(plausible, key=lambda t: abs(t - end)) if plausible else end

    if new_end - new_start < 0.4:   # 吸附結果不合理（例如兩邊吸到同一處）就不採用
        return start, end
    # 最終長度防呆：上面只擋了句尾候選，句首往後吸太多一樣會把句子開頭截掉，
    # 兩邊各自合理但相加後過短的情況也要擋。整體長度撐不住就整組退回原估計。
    if (new_end - new_start) < MIN_DUR_RATIO * est_dur:
        return start, end
    return new_start, new_end


def extract_audio(video, start, end, out, snap=True):
    """依 start/end 切出音檔；snap=True 時先做靜音吸附校正邊界。

    不論是否吸附，都補上同樣的頭尾餘裕（SNAP_HEAD_PAD / SNAP_TAIL_PAD）。

    注意不要再疊加固定緩衝：早期版本在這裡另外加了「前 -0.15s、後 +0.3s」，
    與呼叫端 _mk_sentence 自帶的句尾緩衝相加，最多多切 0.6 秒而吃到下一句。
    現在邊界由時間來源（json3 逐字時間戳或靜音吸附）決定，只補極小的自然餘裕。
    """
    if snap:
        start, end = snap_boundaries(video, start, end)
    # 餘裕不綁在吸附分支裡：這兩件事互相獨立，早期版本綁在一起，導致「不吸附」
    # 的路徑連帶失去頭尾餘裕，句首起音和句尾殘響會被切得很乾。
    start = max(0.0, start - SNAP_HEAD_PAD)
    end = end + SNAP_TAIL_PAD
    dur = max(0.5, end - start)
    # -af aresample=async=1：少數來源（實測 T9LkN-79rfI）的 AAC 解碼幀會讓 libmp3lame
    # 報 "inadequate AVFrame plane padding" 而整個轉檔失敗；插一層重採樣強制重整幀
    # 即可繞過。不指定採樣率，對正常來源等同 no-op，不會改變音質。
    run(["ffmpeg", "-y", "-ss", f"{max(0, start):.3f}", "-i", video,
         "-t", f"{dur:.3f}", "-vn", "-af", "aresample=async=1",
         "-acodec", "libmp3lame", "-q:a", "4", out])


# 跨影片來源的收音品質/後製差異很大（實測範圍可達 -13~-28 dB），統一正規化到
# 這個平均音量基準，避免複習時聲音忽大忽小。
TARGET_VOLUME_DB = -20.0
MAX_GAIN_DB = 15.0      # 增益上限：避免對過度安靜的片段放大到雜訊也一起被放大
CLIP_HEADROOM_DB = -1.0  # 削波保護：套用增益後峰值留一點餘裕，不頂到 0dB 破音


def _measure_volume(path):
    """用 ffmpeg volumedetect 量測平均/峰值音量(dB)；分析不出來則回傳 (None, None)。"""
    result = subprocess.run(
        ["ffmpeg", "-i", path, "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    mean = re.search(r"mean_volume:\s*([\-\d.]+) dB", result.stderr)
    peak = re.search(r"max_volume:\s*([\-\d.]+) dB", result.stderr)
    if not mean or not peak:
        print(f"  (音量分析失敗，跳過正規化：{path}，"
              f"ffmpeg exit={result.returncode})")
        return None, None
    return float(mean.group(1)), float(peak.group(1))


def normalize_audio(path, target_db=TARGET_VOLUME_DB):
    """把句子音檔的平均音量正規化到統一基準。

    用「量測平均音量→計算精確增益」而非 ffmpeg 的 loudnorm，因為句子音檔通常只有
    幾秒，loudnorm 的 EBU R128 積分響度演算法對短音檔誤差較大（實測誤差可達 3dB、
    且同樣長度的片段結果還會不一致）；簡單的增益調整對短音檔反而更精準可預測。
    """
    mean, peak = _measure_volume(path)
    if mean is None:
        return  # 分析失敗（例如極端安靜/近乎無聲的片段），保留原始音檔，不強行處理
    gain = target_db - mean
    gain = max(-MAX_GAIN_DB, min(MAX_GAIN_DB, gain))
    if peak + gain > CLIP_HEADROOM_DB:
        gain = CLIP_HEADROOM_DB - peak
    if abs(gain) < 0.5:
        return  # 差異太小，不值得重新編碼一次
    tmp = f"{path}.norm.mp3"
    run(["ffmpeg", "-y", "-i", path, "-af", f"volume={gain:.2f}dB",
         "-acodec", "libmp3lame", "-q:a", "4", tmp])
    os.replace(tmp, path)


def extract_image(video, t, out):
    run(["ffmpeg", "-y", "-ss", f"{t:.3f}", "-i", video,
         "-frames:v", "1", "-vf", "scale=480:-1", "-q:v", "4", out])


def _http_get_json(url, timeout=10, retries=2, backoff=0.8):
    """抓 JSON，對「暫時性」網路錯誤重試。

    MW/Google 這些端點偶發 SSL handshake timeout（實測 _ssl.c:1011）或連線中斷，
    單次失敗就讓整張卡的定義/同義字/翻譯欄位空白。這類錯誤絕大多數重試一次就好，
    所以做幾次退避重試；HTTP 4xx（如查無此字）不重試，直接往上拋。
    """
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            return json.load(urllib.request.urlopen(req, timeout=timeout))
        except urllib.error.HTTPError:
            raise                       # 4xx/5xx 是伺服器明確回應，重試多半無用
        except Exception as ex:         # timeout / SSL / 連線重置等暫時性錯誤
            last = ex
            if attempt < retries:
                time.sleep(backoff * (attempt + 1))
    raise last


def _mw_get(ref, key, word):
    url = f"{MW_BASE}/{ref}/json/{urllib.parse.quote(word)}?key={key}"
    return _http_get_json(url, timeout=10)


def _ise_to_ize(word):
    """英式 -ise 動詞轉美式 -ize 拼法。

    -ise 結尾也有 promise/surprise/comprise 這類非動詞衍生詞，替換成 -ize 版本
    在 MW 一樣查無此字，跟原本一樣拿不到東西，不會比現在更糟。
    """
    if word.lower().endswith("ise") and len(word) > 4:
        return word[:-3] + "ize"
    return None


def _ller_to_ler(word):
    """英式雙l名詞（traveller/counsellor/jeweller）轉美式單l拼法。

    只在字尾符合 -ller/-llor 時轉換（去掉一個l），不會誤傷 caller/seller/teller/
    smaller 這類本來就雙l、美式英式拼法一致的字——因為這些字直接查詢就能在 MW
    找到對應的 headword，根本不會走到這個備援分支；只有「查無此字」時才會嘗試這個
    轉換，所以最壞情況跟現在一樣查無定義，不會比現在更糟。
    """
    w = word.lower()
    if w.endswith("ller") and len(word) > 5:
        return word[:-4] + "ler"
    if w.endswith("llor") and len(word) > 5:
        return word[:-4] + "lor"
    return None


# MW（美式辭典）常常不收英式拼法為 headword，實測多個常見英式字（prioritise/
# organise/realise/recognise...用-ise、traveller/counsellor/jeweller...用雙l）
# 查詢時 headword 完全比對 100% 失敗、定義留空——這不是 simplemma 的還原錯誤
# （這些字本身就是合法原形，不該被 LEMMA_OVERRIDES 覆寫掉，那樣會讓卡片顯示美式
# 拼法而非影片實際講的字），純粹是 MW 收錄拼法的限制，該在查詢層做備援，不動卡片
# 顯示的原始拼法。依序嘗試這份清單，第一個在 MW 查得到的就採用。
_SPELLING_VARIANTS = [_ise_to_ize, _ller_to_ler]


def _filter_homographs(data, word, from_thesaurus=False):
    """從 MW API 回傳的 entry 陣列中，只留 headword 完全等於 word 的詞條。

    避免 MW 查無時混入的相近字詞條（metabolic→metabolism）或誤抓到的組合詞
    （pedal→soft-pedal）。learners 用 hwi.hw，thesaurus 用 meta.id。
    """
    out = []
    for entry in data:
        if not isinstance(entry, dict):
            continue  # 查無此字時 API 回傳拼字建議字串
        if from_thesaurus:
            hw = entry.get("meta", {}).get("id", "").split(":")[0]
        else:
            hw = entry.get("hwi", {}).get("hw", "")
        hw = re.sub(r"[*]", "", hw).lower()
        if hw == word.lower():
            out.append(entry)
    return out


# 查詢結果用具名欄位而非位置元組：這個函式先前從回傳 2 個值改成 3 個，散在各處的
# 位置解包沒有全部跟上（實測 tools/backfill_tags.py 漏改，一跑就 ValueError），
# 具名存取不會有這個問題。
MWLookup = collections.namedtuple("MWLookup", "homographs matched_word raw")


def _mw_lookup_with_fallback(ref, key, word, from_thesaurus=False):
    """查 MW，headword 比對不到東西時依序嘗試 _SPELLING_VARIANTS 的拼法備援。

    回傳 MWLookup(homographs, matched_word, raw)。raw 是「原字」的完整回應，讓呼叫端
    在查無 headword 時能繼續在同一份回應裡找衍生詞（見 _find_run_on），不必重查一次
    API。都查無時 homographs 為空 list。
    """
    data = _mw_get(ref, key, word)
    homographs = _filter_homographs(data, word, from_thesaurus)
    if homographs:
        return MWLookup(homographs, word, data)
    for variant_fn in _SPELLING_VARIANTS:
        alt = variant_fn(word)
        if not alt:
            continue
        try:
            alt_data = _mw_get(ref, key, alt)
            homographs = _filter_homographs(alt_data, alt, from_thesaurus)
            if homographs:
                return MWLookup(homographs, alt, data)
        except Exception:
            pass  # 備援查詢本身失敗（逾時等）就當作沒查到，換下一個變體或放棄
    return MWLookup([], word, data)


# 指人的衍生字尾。母詞定義套不到這類字上：psychotherapist 拿到的會是 psychotherapy
# 的「用談話治療心理疾病」——那是療法的定義，不是「治療師」這個人。單看詞性擋不掉，
# 兩者都是名詞。相對地 -ity／-ness／-ion 這類抽象名詞（vulnerability ← vulnerable）
# 母詞定義還能轉換過去，予以保留。
_AGENT_SUFFIX = re.compile(r"(?:ist|er|or|ee|eer|ician)$", re.IGNORECASE)



def _find_run_on(data, word):
    """headword 對不上時，看看這個字是不是掛在某個詞條底下的派生詞。

    MW 把 -ly 副詞、-ity 名詞、-ist 名詞這類「意思可以從母詞推得」的衍生詞放在母詞
    條目的 uros 裡，只給詞性、發音和例句，**不給定義**（字典稱為 undefined run-on）。
    查 humbly 時 MW 回的是 humble 的條目，headword 比對不上就整條被丟掉、定義留空——
    實測一支 TED 影片 20 張卡有 6 張定義空白，其中 3 張是這個原因。

    回傳 (母詞, 派生詞的詞性, 母詞的 shortdef list)；不適用時回傳 None。

    界線是「會讀錯的擋掉，只是需要轉換的保留」——後者已經標明了母詞，讀者自己
    轉得過來；前者不管標不標都是錯的。

    擋掉兩種：

      - **母詞是動詞、衍生詞不是**：動詞定義寫成「to do X」，套到名詞或形容詞上
        語法就不成句——masturbate 的「to touch or rub…」掛在 masturbation 上、
        enjoy 的「to take pleasure in」掛在 enjoyable 上都是這樣。
      - **指人的衍生詞**（-ist/-er/-or…）：psychotherapist 會拿到 psychotherapy 的
        「用談話治療心理疾病」——那是療法不是人，語意直接錯了。單看詞性擋不掉，
        兩邊都是名詞，只能看字尾。

    保留的例如 humble→humbly（形容詞→副詞，直接套用就通）、vulnerable→vulnerability
    （形容詞→名詞）、evolution→evolutionary（名詞→形容詞，讀者自行轉換）。

    另外，同時命中多個母詞時也放棄，因為分不出該用哪一個。
    """
    target = word.lower()
    hits = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        shortdefs = entry.get("shortdef") or []
        if not shortdefs:
            continue
        base = re.sub(r"[*]", "", entry.get("hwi", {}).get("hw", ""))
        for uro in entry.get("uros") or []:
            if re.sub(r"[*]", "", uro.get("ure", "")).lower() == target:
                hits.append((base, entry.get("fl", ""), uro.get("fl", ""), shortdefs))
                break                      # 同一個詞條命中一次就夠，母詞定義是同一組
    if len(hits) != 1:
        return None                        # 沒命中，或命中多個母詞而無從選擇
    base, base_fl, fl, shortdefs = hits[0]
    if base_fl == "verb" and fl != "verb":
        return None
    if fl == "noun" and _AGENT_SUFFIX.search(target):
        return None
    return base, fl, shortdefs


def _mw_clean(text):
    """去掉 MW 的標記符號（{bc}、{it}...{/it}、{sx|...}、—often 等）。"""
    text = re.sub(r"\{bc\}", ": ", text)
    text = re.sub(r"\{/?it\}", "", text)
    text = re.sub(r"\{sx\|([^|}]*)\|*[^}]*\}", r"\1", text)
    text = re.sub(r"\{[^}]*\}", "", text)
    return html.escape(text.strip(" :"))


# 猜句中 surface 詞性用的簡單訊號詞（規則式，不追求完美，只求比「盲抓第一條」準）
_POS_MODALS = {"will", "would", "can", "could", "shall", "should", "may", "might", "must"}
# 完成式/進行式的助動詞：「have drifted」的 drifted 是動詞，但 MW 把 drift 的名詞義
# 排在前面，沒認出助動詞就會抓到「漂移(名詞)」這種對不上句子的詞條。
_POS_AUXILIARIES = {"have", "has", "had", "having",
                    "do", "does", "did", "don't", "doesn't", "didn't"}
_POS_DETERMINERS = {"a", "an", "the", "my", "your", "his", "her", "its", "our", "their",
                     "this", "that", "these", "those", "some", "any", "no", "each", "every"}
_POS_LINKING = {"is", "are", "was", "were", "be", "been", "being",
                 "seem", "seems", "seemed", "become", "becomes", "became",
                 "feel", "feels", "felt"}
# 連綴動詞和表語形容詞間常夾一個副詞（"is already awake"、"was still tired"），
# 往前多看一格時先跳過這類詞，不然只看緊鄰前一個字會漏判。
_POS_ADVERBS_BETWEEN = {"already", "still", "just", "very", "so", "too",
                         "quite", "rather", "really", "almost", "not"}
_NOUN_SUFFIXES = ("tion", "sion", "ment", "ness", "ity", "ety", "ism", "ship", "hood",
                  "ance", "ence", "dom")
_ADJ_SUFFIXES = ("ical", "ic", "ous", "ive", "able", "ible", "ful", "less")
_ADV_SUFFIXES = ("ly",)


def _guess_pos(surface, sentence):
    """規則式猜 surface 在句中的詞性（"to word" → verb、"a/the word" → noun 等），
    猜不出回傳 None。用來在 MW 回傳同一headword的多個詞性(名詞/動詞...)時挑對的那條，
    而非盲目取第一條——例如 "to pedal" 是動詞，MW 卻把名詞義排在前面。"""
    tokens = re.findall(r"[A-Za-z][A-Za-z'-]*", sentence)
    idx = next((i for i, t in enumerate(tokens) if t.lower() == surface.lower()), None)
    if idx is None:
        return None
    prev = tokens[idx - 1].lower() if idx > 0 else ""
    prev2 = tokens[idx - 2].lower() if idx > 1 else ""
    nxt = tokens[idx + 1].lower() if idx + 1 < len(tokens) else ""
    if prev == "to" or prev in _POS_MODALS or prev in _POS_AUXILIARIES:
        return "verb"
    if prev in _POS_DETERMINERS:
        return "noun"
    if prev in _POS_LINKING:
        return "adjective"
    if prev in _POS_ADVERBS_BETWEEN and prev2 in _POS_LINKING:
        return "adjective"
    if nxt in _POS_DETERMINERS:   # 「Laura straps a mask」：後面接冠詞/所有格 → 及物動詞+受詞
        return "verb"
    low = surface.lower()
    if low.endswith(_ADV_SUFFIXES):
        return "adverb"
    if low.endswith(_NOUN_SUFFIXES):
        return "noun"
    if low.endswith(_ADJ_SUFFIXES):
        return "adjective"
    return None


# 比對用停用詞。除了功能詞，特別要擋「英英定義的樣板高頻詞」——person/thing/way
# 這類在幾乎每條定義裡都會出現，納入比對會製造假重疊（實測 communicate 因為疾病義
# 定義含 "person"、卡片也含 "person" 就被誤選成「傳染疾病」）。有區別力的內容動詞
# （deal/change/word…）刻意不擋，那是真正的詞義訊號來源。
_MATCH_STOP = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "at", "for",
    "with", "by", "from", "as", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "can", "could",
    "not", "no", "that", "this", "these", "those", "it", "its", "you", "your",
    "he", "she", "they", "them", "his", "her", "their", "we", "our", "i", "me",
    "my", "so", "if", "then", "than", "such", "used", "usually", "often",
    # 定義樣板高頻詞（幾乎每條定義都有，無區別力）
    "something", "someone", "somebody", "some", "any", "etc", "person", "people",
    "thing", "things", "way", "ways", "one", "another", "other", "others",
    "make", "makes", "made", "making", "cause", "causes", "become", "becomes",
    "get", "gets", "kind", "sort", "type", "particular", "especially", "usually",
}


def _stem(w):
    """只去 -ing/-ed 動詞尾，補 simplemma 的漏網（實測 lemma('dealing')='dealing' 未
    還原成 deal），讓卡片句的 'dealing' 對上 MW 定義裡的 'deal'。

    刻意不碰 -s/-es：simplemma 已能正確還原規則複數（words→word、tricks→trick），
    自己去 -s 反而會把 news→new、lens→len 這種切成無關真字，製造假重疊選錯詞義。"""
    for suf in ("ingly", "ing", "edly", "ed"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[:-len(suf)]
    return w


def _content_lemmas(text):
    """把文字轉成內容詞的 lemma 集合，供詞義比對用。

    每個詞同時放入 lemma 與再去尾的 stem 兩種形式，提高跨形態的比對召回
    （deal↔dealing、require↔requiring）。這是模糊重疊評分，多召回利大於弊。
    """
    from autopick import lemma      # 既有依賴；lazy import 保持 mine 啟動輕量
    text = re.sub(r"\{[^}]*\}", " ", text)       # 去 MW 標記 {bc}{it}{sx|..}
    out = set()
    # 涵蓋拉丁重音字母（同 autopick.WORD_CHARS）：只用 [a-z] 會把 appétit 切成
    # app+tit（tit 恰是真字，造成假重疊選錯詞義），整詞保留才不會誤配。
    for w in re.findall(r"[a-zà-öø-ÿ]+", text.lower()):
        if len(w) >= 3 and w not in _MATCH_STOP:
            lm = lemma(w)
            out.add(lm)
            out.add(_stem(lm))
    return out


def _sense_scores(shortdefs, sentence, surface):
    """每個候選義項與例句的內容詞重疊分數。

    _pick_sense（選哪一個）與 _sense_is_a_guess（這次選擇有沒有依據）共用這裡，
    兩邊各算一次的話，改了其中一邊就會讓提示跟實際選擇對不上。
    回傳空 list 代表「無從比較」：只有一個義項、沒有例句、或例句沒有可比對的內容詞。
    """
    if len(shortdefs) <= 1 or not sentence:
        return []
    ctx = _content_lemmas(sentence) - _content_lemmas(surface or "")
    if not ctx:
        return []
    return [len(ctx & _content_lemmas(sd)) for sd in shortdefs]


def _pick_sense(shortdefs, sentence, surface):
    """在 shortdef 的多個定義中，用『例句 vs 各定義的內容詞重疊』選最貼合語境的那條。

    只用 shortdef（MW 官方整理的前三義乾淨清單），不自己走 def→sseq 樹——實測那棵樹
    會漏掉主定義又抓進 {dx} 交叉引用垃圾（"see also"/"opposite"）。shortdef 乾淨可靠，
    通常 2-3 條，正確詞義在其中的機率高。

    回傳最佳定義 index。重疊全為 0（訊號不足）時回傳 0＝退回第一義(舊行為)，
    保證只在有把握時改善、不會讓原本對的變錯。穩定 argmax：同分取較前者。
    """
    best_i, best_score = 0, 0
    for i, score in enumerate(_sense_scores(shortdefs, sentence, surface)):
        if score > best_score:
            best_i, best_score = i, score
    return best_i


def _sense_is_a_guess(shortdefs, sentence, surface):
    """這次的詞義選擇是不是「沒有依據的猜測」。

    _pick_sense 靠例句與各義項的內容詞重疊挑義項；重疊全為 0 時它沒有任何依據，
    只是退回第一義。實測這種情況相當常有挑錯：38 張人工核對過的卡裡，符合這個
    條件的有 20 張，其中 12 張的詞義確實不對。

    但它只是「值得看一眼」的訊號，不是判定：
    - 另外 8 張是誤報，選對了也可能一個詞都沒重疊（steak、thankful 這類）
    - 反過來也漏得掉：accent / minimize / vague 拿到了 1 分所以不會被標記，
      實際上正是被那 1 分推向錯誤答案的（見 repo issue #3）
    單一義項的詞條沒得挑，不算猜測。
    """
    scores = _sense_scores(shortdefs, sentence, surface)
    return bool(scores) and max(scores) == 0


def _entry_is_a_guess(homographs, wanted_pos):
    """「挑哪個詞條」是不是盲選的——比義項挑錯更嚴重，整條定義的詞性都會不對。

    _guess_pos 猜不出句中該字的詞性、而這個字在字典裡有多個不同詞性的詞條時，
    fetch_definition 只能退回 homographs[0]（字典排第一的那個）。實例：例句講的是
    "train fares"（名詞，票價），但 fare 的第一個詞條是動詞，於是卡片寫成
    「to do something well or badly」，整條都對不上。

    兩種情況都算盲選，因為 fetch_definition 的 next(..., homographs[0]) 對兩者
    的結果是一樣的：
      1. _guess_pos 回 None——句子裡看不出詞性；
      2. 猜出了詞性，但字典裡根本沒有那個詞性的詞條（例如猜 verb、字典只收
         noun/adjective）——這時 next() 一樣會靜靜退回第一個詞條。

    這個判斷是結構性的（「沒有依據可選」是事實，不是猜測），所以比 _sense_is_a_guess
    精確；後者只代表「沒有訊號」，而沒訊號多半只是因為例句與字典用詞不同。
    """
    available = {e.get("fl") for e in homographs}
    if len(available) <= 1:
        return False                    # 只有一種詞性，退回第一個不是猜
    return wanted_pos not in available


# 最近一次 fetch_definition 的不確定旗標（"pos" / "sense"）。add_card 讀它決定要在
# note 上打哪些 tag——卡面刻意維持不變（Anki 的最小資訊原則），不確定性放進 tag，
# 之後在 Anki 用 tag:pos-uncertain 就能把該複查的整批撈出來，複習時完全看不到。
# 用模組層變數而非回傳值，是因為 fetch_definition 的回傳型別（定義字串）已被多處
# 使用，改成 tuple 會波及每個呼叫端，對一個附帶資訊不值得。
LAST_FLAGS = set()

# 旗標與 Anki 標籤的對應。定義查不到（nodef）也算一種「這張卡不完整」，而且比
# 挑錯義項更明顯——卡片背面直接是空的——卻反而沒有任何痕跡，所以一併納入。
UNCERTAIN_TAG = {"pos": "pos-uncertain", "sense": "sense-uncertain",
                 "nodef": "no-definition", "lookupfail": "definition-lookup-failed"}

# 整批建卡的彙總，--auto 跑完列出來。手動 --index 一次只做一張，逐張提示就夠。
UNCERTAIN = []   # [(word, frozenset(flags))]


def fetch_definition(word, sentence="", surface=""):
    """Merriam-Webster Learner's Dictionary：乾淨、學習者導向的定義。

    兩層消歧：
      1. headword 過濾＋詞性推測（_guess_pos）挑對「哪個詞條」——處理同形異義字
         （pedal 名詞/動詞）、以及 MW 查無時夾帶的相近字詞條（metabolic→metabolism）。
      2. 在選定詞條的多個詞義中，用例句與各詞義文字/例句的內容詞重疊挑對「哪個詞義」
         ——處理同詞性不同義（endure=忍受 vs 持續存在、tricky=棘手 vs 狡猾）。
    訊號不足時兩層都安全退回舊行為（POS 第一條詞條、MW 第一個詞義）。
    """
    LAST_FLAGS.clear()
    if not MW_LEARNERS_KEY:
        return ""
    try:
        found = _mw_lookup_with_fallback("learners", MW_LEARNERS_KEY, word)
        homographs = found.homographs
        if not homographs:
            run_on = _find_run_on(found.raw, word)
            if run_on:
                base, fl, base_defs = run_on
                # 派生詞在字典裡沒有自己的定義，只能用母詞的。標明「某字的某某形」，
                # 否則會出現詞性與定義內容打架的情況——vulnerability 標 noun，
                # 定義文字卻是形容詞的「easily hurt or harmed」。
                idx = _pick_sense(base_defs, sentence, surface)
                return (f"<i>{html.escape(fl)}</i>（{html.escape(base)} 的衍生詞）"
                        f"{_mw_clean(base_defs[idx])}")
            LAST_FLAGS.add("nodef")
            print(f"  ⚠ 查無定義：{word} 在 Merriam-Webster Learner's 沒有詞條，"
                  f"也不是任何詞條的衍生詞")
            return ""
        wanted_pos = _guess_pos(surface or word, sentence) if sentence else None
        if _entry_is_a_guess(homographs, wanted_pos):
            LAST_FLAGS.add("pos")
            kinds = ", ".join(sorted({e.get("fl", "?") for e in homographs}))
            print(f"  ⚠ 詞性可能挑錯：{word} 在字典有多種詞性（{kinds}），"
                  f"但句子裡看不出是哪一種，只能用排第一的")
        entry = next((e for e in homographs if e.get("fl") == wanted_pos), homographs[0])
        pos = entry.get("fl", "")
        shortdefs = entry.get("shortdef", [])
        if not shortdefs:
            LAST_FLAGS.add("nodef")
            print(f"  ⚠ 查無定義：{word} 在字典裡有詞條，但沒有可用的簡明釋義")
            return ""
        idx = _pick_sense(shortdefs, sentence, surface)
        if _sense_is_a_guess(shortdefs, sentence, surface):
            LAST_FLAGS.add("sense")
            print(f"  ⚠ 詞義可能挑錯：{word} 有 {len(shortdefs)} 個候選字義，"
                  f"但沒有一個對得上例句，只能退回第一個")
        return f"<i>{html.escape(pos)}</i> {_mw_clean(shortdefs[idx])}"
    except Exception as ex:
        # 網路錯誤、逾時、額度用盡都走這裡。這跟「字典沒收這個字」是兩回事，
        # 但兩者的 Definition 欄都是空的——不分開標記的話，事後完全看不出某張
        # 卡是「查不到」還是「當時沒查成」。
        LAST_FLAGS.add("lookupfail")
        print(f"  (定義查詢失敗：{ex})")
        return ""


# 這是 Google 翻譯網頁版的內部端點，非官方、沒有保證。client 參數決定它套用哪套
# 配額政策：實測 2026-08 起 client=gtx 一律回 429，且與來源 IP 無關（學校直連、
# 學校 VPN、行動網路三個不同出口都一樣），也與 User-Agent 無關（三種都試過）——
# 是這個 client 被整個限制掉，不是被當成濫用來源擋。dict-chrome-ex 仍然可用，
# 回傳格式與 gtx 相同。兩個都留著依序試：哪一個哪天失效無從預測，備援成本極低。
_TRANSLATE_CLIENTS = ("dict-chrome-ex", "gtx")


def _google_translate(text):
    first_error = None
    for client in _TRANSLATE_CLIENTS:
        url = ("https://translate.googleapis.com/translate_a/single"
               f"?client={client}&sl=en&tl=zh-TW&dt=t&q=" + urllib.parse.quote(text))
        try:
            data = _http_get_json(url, timeout=10)
        except urllib.error.HTTPError as ex:
            # 只有伺服器明確回絕（配額用盡、封鎖）才值得換下一個 client。
            # timeout / SSL / 連線重置這類暫時性錯誤 _http_get_json 內部已經重試過，
            # 這裡再換 client 等於同一次故障打兩倍的請求——而反覆請求正是最容易
            # 加速被限流的事，所以讓它直接往上拋。
            first_error = first_error or ex     # 留第一個（偏好 client）的失敗，較有診斷價值
            continue
        # 解析放在 try 外面：回傳格式不如預期是程式問題，不該被當成「這個 client
        # 不可用」而靜靜換下一個，那會把真正的 bug 藏起來。
        return "".join(seg[0] for seg in data[0] if seg[0])
    raise first_error


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
                        # 翻譯服務偶爾會把結果包在 HTML 標籤裡（實測遇過整段被
                        # <div>...</div> 包住），先清掉避免殘留標籤污染欄位。
                        candidate = re.sub(r"<[^>]+>", "", combined.split(sep, 1)[0]).strip()
                        if candidate and not re.search(r"[A-Za-z]", candidate):
                            return html.escape(candidate)
                        break  # 翻譯失敗（殘留英文）→ 跳出，改走下面的裸字翻譯
            except Exception:
                pass  # 帶語境翻譯呼叫本身失敗（逾時等）→ 一併退回裸字翻譯
    try:
        bare = re.sub(r"<[^>]+>", "", _google_translate(word)).strip()
        return html.escape(bare)
    except Exception as ex:
        print(f"  (中文翻譯失敗：{ex})")
        return ""


def fetch_synonyms(word, n_syn=6, n_ant=4, sentence="", surface=""):
    """Merriam-Webster Thesaurus：同反義字。

    同 fetch_definition：先過濾成 headword 真的等於查詢字的詞條（避免 "pedal"
    誤抓到 "soft-pedal" 的同義字），再挑詞性符合句子語境的那條。
    """
    if not MW_THESAURUS_KEY:
        return ""
    try:
        found = _mw_lookup_with_fallback(
            "thesaurus", MW_THESAURUS_KEY, word, from_thesaurus=True)
        homographs, hit_word = found.homographs, found.matched_word
        if not homographs:
            return ""
        wanted_pos = _guess_pos(surface or word, sentence) if sentence else None
        entry = next((e for e in homographs if e.get("fl") == wanted_pos), homographs[0])
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
        return "<br>".join(parts)
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


def add_card(video_id, video_file, sent, word, title, collocation="", highlight_word=None,
             save_image=False, legacy_snap=False):
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

    # 句子時間來自 json3 逐字時間戳時跳過靜音吸附。實測在那批樣本上，吸附把已經
    # 夠準的邊界又推移了 0.35~0.75 秒（正好是一到三個字），反而切掉句首或多帶進
    # 隔壁句：42 張 ASR 比對「句子不完整」10→5，其中 10 張的人耳盲測 4→0。
    # 推測是 SNAP_WINDOW 有 1.2 秒，在連續語流裡吸到了句子「內部」的停頓——但這
    # 只是假說，沒有標註被選中的靜音點落在句內還是句間。
    # 證據的侷限：樣本是 3 支同類型（對話式教學）影片，語速快、句間停頓短，正好
    # 是吸附最容易誤判的情境；量測工具本身在 ±180ms 內不可信，所以無法斷定小幅
    # 差異。其他語速或錄音條件沒有驗證過——覺得不對可以用 --legacy-snap 退回。
    # 沒有 json3 的影片（人工字幕）時間只能靠 SRT 線性內插，誤差大得多，吸附是
    # 管線目前僅有的校正手段，維持原本行為。詳見 repo issue #5。
    extract_audio(video_file, sent["start"], sent["end"], f"{MEDIA_DIR}/{audio_fn}",
                  snap=legacy_snap or not sent.get("from_json3", False))
    normalize_audio(f"{MEDIA_DIR}/{audio_fn}")
    store(f"{MEDIA_DIR}/{audio_fn}", audio_fn)
    if save_image:
        extract_image(video_file, mid, f"{MEDIA_DIR}/{img_fn}")
        store(f"{MEDIA_DIR}/{img_fn}", img_fn)

    surface = highlight_word or word
    definition = fetch_definition(word, sentence=sent["text"], surface=surface)
    # 立刻複製：LAST_FLAGS 屬於「最近一次查詢」，下面還會呼叫其他 fetch_*
    flags = frozenset(LAST_FLAGS)
    synonyms = fetch_synonyms(word, sentence=sent["text"], surface=surface)
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
            "Image": f'<img src="{html.escape(img_fn, quote=True)}">' if save_image else "",
            "Source": html.escape(title),
            "URL": f'<a href="{html.escape(url, quote=True)}">{html.escape(url)}</a>',
        },
        # 不確定性放 tag 不放卡面：複習時看到的東西完全不變（卡片資訊越少記憶效果
        # 越好），但在 Anki 搜 tag:pos-uncertain 就能把該複查的整批撈出來。
        "tags": (["youtube-mining", video_id]
                 + [UNCERTAIN_TAG[k] for k in ("pos", "sense", "nodef", "lookupfail")
                    if k in flags]),
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
    print(f"  Audio: {audio_fn} / Image: {img_fn if save_image else '(未留存)'}")
    print(f"  URL: {url}")
    if flags:
        # 記在這裡而不是 fetch_definition 裡：這樣彙總清單與卡片上的 tag 永遠一致，
        # 不會出現「彙總列了但卡片沒建成」或「卡片有 tag 但彙總漏掉」。
        UNCERTAIN.append((word, flags))
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
    ap.add_argument("--legacy-snap", action="store_true",
                    help="對有 json3 逐字時間戳的句子也做靜音吸附（改動前的行為）。"
                         "新的預設是跳過吸附，實測切點較準；這個旗標是退路，"
                         "用於那批樣本沒涵蓋到的素材（見 issue #5）")
    ap.add_argument("--with-image", action="store_true",
                    help="擷取/儲存影片截圖；預設不留存（Image 欄位留空），需要才加這個旗標")
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

    # 數值參數驗證：負數/顛倒的區間不會直接報錯，而是觸發 Python slice 或
    # range 比較的非直覺行為（如 --max-cards -1 會靜默少做一張卡），提早擋掉
    if args.max_cards < 1:
        ap.error(f"--max-cards 需為正整數（收到 {args.max_cards}）")
    if args.min_words < 1 or args.max_words < args.min_words:
        ap.error(f"--min-words/--max-words 需為正整數且 min ≤ max"
                 f"（收到 {args.min_words}/{args.max_words}）")
    # NaN 會讓所有比較都是 False：不但通過下面的區間檢查，還會讓 autopick 的
    # z < min / z > max 頻率篩選整個形同失效，必須先擋掉
    if not (math.isfinite(args.min_zipf) and math.isfinite(args.max_zipf)):
        ap.error(f"--min-zipf/--max-zipf 需為有限數值"
                 f"（收到 {args.min_zipf}/{args.max_zipf}）")
    if args.min_zipf > args.max_zipf:
        ap.error(f"--min-zipf 不可大於 --max-zipf"
                 f"（收到 {args.min_zipf}/{args.max_zipf}）")
    if args.index is not None and args.index < 0:
        ap.error(f"--index 需為非負整數（收到 {args.index}）")

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

    # 有 json3 逐字時間戳就拿它校正句子時間（比 SRT 內插準得多）
    json3 = f"{MEDIA_DIR}/{args.video_id}.en.json3"
    refined = refine_with_json3(sents, json3)
    if refined:
        print(f"已用 json3 逐字時間戳校正 {refined}/{len(sents)} 句的時間軸")

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
        UNCERTAIN.clear()             # 這是單次執行的累計，不是跨次的
        for c in picks:
            try:
                nid = add_card(args.video_id, video, c["sent"], c["lemma"],
                               args.title, highlight_word=c["surface"],
                               save_image=args.with_image,
                               legacy_snap=args.legacy_snap)
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
        pos_bad = [w for w, f in UNCERTAIN if "pos" in f]
        sense_bad = [w for w, f in UNCERTAIN if "sense" in f]
        nodef = [w for w, f in UNCERTAIN if "nodef" in f]
        failed = [w for w, f in UNCERTAIN if "lookupfail" in f]
        if pos_bad or sense_bad or nodef or failed:
            print("\n⚠ 這些卡有需要留意的地方，已打上 tag，可在 Anki 搜尋列篩出來複查：")
            if pos_bad:
                print(f"   tag:pos-uncertain（{len(pos_bad)} 個）  " + "、".join(pos_bad))
                print("     字典裡有多種詞性，但句子看不出是哪一種——整條定義的詞性"
                      "都可能不對，這類最值得優先看")
            if sense_bad:
                print(f"   tag:sense-uncertain（{len(sense_bad)} 個）  " + "、".join(sense_bad))
                print("     詞性對了，但同一詞性下的幾個意思沒有一個對得上例句")
            if nodef:
                print(f"   tag:no-definition（{len(nodef)} 個）  " + "、".join(nodef))
                print("     字典查不到這個字，Definition 欄是空的")
            if failed:
                print(f"   tag:definition-lookup-failed（{len(failed)} 個）  " + "、".join(failed))
                print("     查詢當下失敗了（網路、逾時、額度用盡），不是字典沒收——"
                      "改天重跑同一支影片就會補上")
            print("   注意：沒被標記不代表就是對的——管線「有把握地選錯」的情況"
                  "抓不到（實測樣本裡約每 3 個錯誤會漏掉 1 個）")
        return

    if not (0 <= args.index < len(sents)):
        ap.error(f"--index {args.index} 超出範圍（0~{len(sents)-1}）；可先用 --list 看索引")
    sent = sents[args.index]
    add_card(args.video_id, video, sent, args.word, args.title, args.collocation,
             save_image=args.with_image, legacy_snap=args.legacy_snap)


if __name__ == "__main__":
    main()
