"""比較「靜音吸附」與「直接用 json3 逐字時間戳」哪一種切點比較準。

## 這支工具在量什麼

管線切句子音檔前要先決定起訖時間。有兩個來源：

1. json3 逐字時間戳（YouTube 自動字幕才有）——`refine_with_json3` 會用它覆寫
2. 靜音吸附 `snap_boundaries`——偵測真實音訊，把邊界吸到最近的停頓

原本兩個都用：先取 json3 的時間，再吸附一次。這支工具驗證第二步是幫忙還是
幫倒忙，做法是對同一批句子各切兩版音檔、都跑語音辨識、再跟例句比對：

- **A** = 吸附後加頭尾餘裕（本工具寫死這個順序，等同改動前的管線行為）
- **B** = 直接用 json3 的邊界加同樣的餘裕

## 這把尺的精度上限（重要）

計分是拿辨識出來的文字跟例句對齊，算句首/句尾漏了或多了幾個字。這個方法只
適合做「同一批句子、改參數前後」的相對比較：

- whisper 的 word timestamps 實測抖動達 ±180ms，比典型的切點差異還大，所以
  **不要用這支工具去調 100ms 級的參數**（例如比較 SNAP_WINDOW 1.2 與 0.6）
- 句首被削掉幾十毫秒時，小模型可能整個詞組都辨識不出來，於是報成「漏了兩個
  字」——這把尺會**高估嚴重度**，絕對數字不可信
- 要在更細的尺度上定論，需要 forced alignment，不是 ASR

## 用法

    python tools/bench_snap.py VIDEO_ID [VIDEO_ID ...]

需要 media/ 下有該影片的 .mp4 / .en.srt / .en.json3，以及一個裝有
openai-whisper 的直譯器（見 tools/asr_timing.py，同樣用 ASR_PYTHON 指定）。
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from difflib import SequenceMatcher

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mine
from asr_timing import find_whisper_python, INSTALL_HINT

MIN_WORDS, MAX_WORDS = 6, 22   # 太短的句子辨識不穩，太長的沒必要
_norm = lambda s: [w for w in re.sub(r"[^a-z0-9' ]", " ", s.lower()).split() if w]


def build_pairs(video_id):
    """走一次正式管線路徑，回傳時間來自 json3 的句子。

    刻意用 refine_with_json3 同一組條件（每句自己的 SRT 起點當提示、預設搜尋
    範圍、en-st>=0.4 門檻），這樣量到的才是管線真的會切的那組邊界。
    """
    srt = f"{mine.MEDIA_DIR}/{video_id}.en.srt"
    j3 = f"{mine.MEDIA_DIR}/{video_id}.en.json3"
    for p in (srt, j3, f"{mine.MEDIA_DIR}/{video_id}.mp4"):
        if not os.path.exists(p):
            sys.exit(f"缺少檔案：{p}")
    sents = mine.build_sentences(mine.parse_srt(srt))
    refined = mine.refine_with_json3(sents, j3)
    if not refined:
        sys.exit(f"{video_id}：沒有任何句子被 json3 校正——這支影片可能是人工字幕，"
                 "本工具不適用（那類影片只能靠吸附，見 issue #1）")
    return [s for s in sents
            if s["from_json3"] and MIN_WORDS <= s["nwords"] <= MAX_WORDS]


def cut_both(video, sent, outdir, idx):
    """對同一句切出 A（吸附）與 B（不吸附）兩版。"""
    out = {}
    for tag, snap in (("A", True), ("B", False)):
        p = os.path.join(outdir, f"{tag}__{idx:04d}.mp3")
        mine.extract_audio(video, sent["start"], sent["end"], p, snap=snap)
        out[tag] = p
    return out


BATCH = 60      # 每次餵給 whisper 的檔案數上限，避免命令列超過 OS 的參數長度限制


def transcribe_dir(d, python_bin, model="base"):
    """把目錄裡的 mp3 分批餵給 whisper，回傳 {檔名主幹: 辨識文字}。

    分批是必要的：一支 20 分鐘的影片就可能產生數百個句子，兩版共上千個路徑，
    單一命令會撞到 OS 的參數長度上限（macOS 約 1MB、Linux 依 stack 而定）。
    """
    mp3s = sorted(f for f in os.listdir(d) if f.endswith(".mp3"))
    if not mp3s:
        return {}
    env = {**os.environ, "KMP_DUPLICATE_LIB_OK": "TRUE"}
    for i in range(0, len(mp3s), BATCH):
        chunk = mp3s[i:i + BATCH]
        print(f"  辨識 {i + 1}-{i + len(chunk)} / {len(mp3s)}", flush=True)
        cmd = [python_bin, "-m", "whisper", *[os.path.join(d, f) for f in chunk],
               "--model", model, "--language", "en", "--output_format", "txt",
               "--output_dir", d, "--fp16", "False"]
        r = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if r.returncode != 0:
            sys.exit(f"whisper 執行失敗（exit {r.returncode}）：\n{r.stderr[-1500:]}")
    out = {}
    for f in mp3s:
        stem = f[:-4]
        txt = os.path.join(d, stem + ".txt")
        if os.path.exists(txt):
            out[stem] = open(txt, encoding="utf-8").read()
    return out


def score(sentence, hyp):
    """對齊辨識結果與例句，回傳句首/句尾各漏了、多了幾個字。

    只適合做同語料的相對比較。辨識誤字若落在句首句尾會被算成切點錯誤，而句首被
    削掉幾十毫秒時小模型可能整組漏聽，於是報成「漏了兩個字」——**這裡回傳的絕對
    數字會高估嚴重度**，不能拿來估問題的實際規模（詳見本檔開頭）。
    """
    T, H = _norm(sentence), _norm(hyp)
    if not T or not H:
        return None
    blocks = [b for b in SequenceMatcher(None, T, H).get_matching_blocks() if b.size]
    if not blocks:
        return None
    first, last = blocks[0], blocks[-1]
    return {"head_miss": first.a, "head_extra": first.b,
            "tail_miss": len(T) - (last.a + last.size),
            "tail_extra": len(H) - (last.b + last.size)}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video_ids", nargs="+")
    ap.add_argument("--model", default="base", help="whisper 模型（預設 base）")
    ap.add_argument("--keep", metavar="DIR", help="保留切出來的音檔到這個目錄，方便人工聽")
    args = ap.parse_args()

    python_bin = find_whisper_python()
    if not python_bin:
        sys.exit(INSTALL_HINT)

    rows = []
    tmp = args.keep or tempfile.mkdtemp(prefix="bench_snap_")
    os.makedirs(tmp, exist_ok=True)
    idx = 0
    index = {}
    for vid in args.video_ids:
        sents = build_pairs(vid)
        print(f"{vid}：{len(sents)} 句可用（時間來自 json3、長度 {MIN_WORDS}-{MAX_WORDS} 字）")
        for s in sents:
            cut_both(f"{mine.MEDIA_DIR}/{vid}.mp4", s, tmp, idx)
            index[idx] = s["text"]
            idx += 1
    print(f"共切 {idx * 2} 個音檔，辨識中（model={args.model}）...")
    hyps = transcribe_dir(tmp, python_bin, args.model)

    for i, text in index.items():
        a, b = hyps.get(f"A__{i:04d}"), hyps.get(f"B__{i:04d}")
        if a is None or b is None:
            continue
        sa, sb = score(text, a), score(text, b)
        if sa and sb:
            rows.append((text, sa, sb))
    if not rows:
        sys.exit("沒有可比對的結果")

    def n_with(key, which, th=1):
        return sum(1 for r in rows if r[which][key] >= th)
    def total(key, which):
        return sum(r[which][key] for r in rows)
    incomplete = lambda r: r["head_miss"] + r["tail_miss"] > 0

    print(f"\n可比對句子：{len(rows)}\n")
    print(f"{'指標':28s} {'A 吸附':>10s} {'B 不吸附':>10s}")
    print("-" * 52)
    for key, label in (("head_miss", "句首漏字"), ("tail_miss", "句尾漏字"),
                       ("head_extra", "句首多帶"), ("tail_extra", "句尾多帶")):
        print(f"{label+'（句數／字數）':28s} "
              f"{str(n_with(key,1))+'／'+str(total(key,1)):>10s} "
              f"{str(n_with(key,2))+'／'+str(total(key,2)):>10s}")
    print("-" * 52)
    print(f"{'句子不完整（有漏字）':28s} "
          f"{sum(1 for r in rows if incomplete(r[1])):>10d} "
          f"{sum(1 for r in rows if incomplete(r[2])):>10d}")
    if args.keep:
        print(f"\n音檔留在 {tmp}（A__ 是吸附版、B__ 是不吸附版）")
    print("\n提醒：這把尺會高估嚴重度，數字只能做同語料的相對比較，"
          "不能拿來估問題的絕對規模（理由見本檔開頭）。")


if __name__ == "__main__":
    main()
