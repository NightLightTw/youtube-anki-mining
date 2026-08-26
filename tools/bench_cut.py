"""量測「沒有逐字時間戳時」的音檔切點誤差。

原理：有 json3 逐字時間戳的影片，本身就帶著標準答案。故意不用它、只餵 SRT 給
管線走內插＋停頓對齊，再跟逐字時間戳比對，就能量出這條路徑的誤差——而那正是所有
人工字幕影片（本專案約 54% 的卡片）唯一能走的路徑。

零額外相依、不需要語音辨識，改完參數可以立刻重跑看分數，適合當調校的內迴圈。

指標只用「字的開始時間」這個 json3 真正記錄的數值，不碰字的結束時間——
parse_json3 的字尾是用 min(下一個字的開始, 起點+MAX_WORD_DUR) 合成的，不是實測
的聲音結束點，拿它當標準答案會讓終點誤差失真。因此這裡量的是兩個「無法辯駁」的
失誤：切點晚於句首第一個字（開頭一定漏掉了）、以及切點晚於下一句第一個字
（一定吃到下一句了）。

務必先讀「已知限制」一節再解讀數字。

用法：
    .venv/bin/python tools/bench_cut.py                 # 跑全部有素材的影片
    .venv/bin/python tools/bench_cut.py -- iDG0rwm9GaQ  # 只跑指定影片
    .venv/bin/python tools/bench_cut.py --json out.json # 另存明細供前後比較
"""
import argparse
import glob
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mine import (  # noqa: E402
    MEDIA_DIR,
    SNAP_HEAD_PAD,
    SNAP_TAIL_PAD,
    _norm_word,
    build_sentences,
    parse_json3,
    parse_srt,
    snap_boundaries,
)

# 標準答案要求逐字全等。這些影片的 SRT 與 json3 出自同一套自動聽打，用字本來就該
# 一致；放寬到部分命中（製卡時用的是 0.6）會讓比對可能對到位移一兩個字的視窗，
# 而位移一個字就足以讓「句首時間」錯得比要量的誤差還大。
TRUTH_MIN_SCORE = 1.0

# 只量「實際上會變成卡片」的句子長度區間，與 mine.py 的 --min-words/--max-words
# 預設一致。這不只是相關性問題，也是標準答案的可信度問題：極短句（"Yeah."、
# "Finance."）在影片裡重複出現很多次，比對很容易配到別的位置，實測看到誤差高達
# 9 秒的樣本全是這類——那是比對配錯，不是切點真的差 9 秒。
BENCH_MIN_WORDS = 6
BENCH_MAX_WORDS = 22

# 判定為明顯瑕疵的門檻（秒）。0.25 秒大致是一個英文單字的長度，超過這個量級
# 開頭就會少掉一個字、或結尾多出一個字，是聽得出來的。這個門檻是為了讓不同
# 版本之間有共同基準，不是什麼精確的心理聲學界線。
BAD_THRESHOLD = 0.25


def unique_truth(words, sentence, hint_start, search=15.0,
                 min_score=TRUTH_MIN_SCORE):
    """回傳 (第一個字的索引, 最後一個字的索引)，無法確定則回傳 None。

    比 mine.locate_sentence 更嚴格：locate_sentence 取分數最高的位置、同分時取先
    找到的那個，這對製卡夠用，但拿來當標準答案會出事——英語教學影片常整句重複
    （實測 "Do you have anything you'd recommend?" 在同一支影片出現多次），配到
    別次出現就會產生十幾秒的假誤差。這裡只要窗內存在兩處以上互不重疊的候選，
    就整句捨棄。寧可少測幾句，也不要拿錯的答案去算分數。
    """
    target = [t for t in (_norm_word(w) for w in sentence.split()) if t]
    if not target or not words:
        return None
    n = len(target)
    hits = []
    for i, (_w, st, _e) in enumerate(words):
        if abs(st - hint_start) > search:
            continue
        window = words[i:i + n]
        if len(window) < n:
            break
        got = [_norm_word(w) for w, _, _ in window]
        score = sum(1 for a, b in zip(target, got) if a == b) / n
        if score >= min_score:
            hits.append((score, i, i + n - 1))
    if not hits:
        return None
    hits.sort(key=lambda h: h[1])
    groups = [[hits[0]]]
    for h in hits[1:]:
        if h[1] <= groups[-1][-1][2]:      # 索引區間重疊 → 視為同一處
            groups[-1].append(h)
        else:
            groups.append([h])
    if len(groups) > 1:
        return None
    best = max(groups[0], key=lambda h: h[0])
    return best[1], best[2]


def has_word_timestamps(json3_path):
    """判斷 json3 是否真的帶逐字時間戳。

    不能只看 parse_json3() 有沒有回傳東西：人工字幕轉出來的 json3 沒有 tOffsetMs，
    每個片段是「一整行字」，parse_json3 仍會回傳非空清單，只是每個元素其實是一整
    行、時間全部等於該行的開始。拿這種資料當標準答案，逐字比對永遠對不上（實測
    5 支 BBC 影片的 677 個候選句全數落空），還會白白耗掉處理時間。
    """
    try:
        data = json.load(open(json3_path, encoding="utf-8"))
    except Exception:
        return False
    return any("tOffsetMs" in s
               for e in data.get("events", [])
               for s in e.get("segs", []))


def video_ids(media_dir=MEDIA_DIR):
    """回傳素材齊全（SRT + 含逐字時間戳的 json3 + mp4）的影片 ID。"""
    out = []
    for srt in sorted(glob.glob(f"{media_dir}/*.en.srt")):
        vid = os.path.basename(srt)[: -len(".en.srt")]
        j3, mp4 = f"{media_dir}/{vid}.en.json3", f"{media_dir}/{vid}.mp4"
        if not (os.path.exists(j3) and os.path.exists(mp4)):
            continue
        if has_word_timestamps(j3) and parse_json3(j3):
            out.append(vid)
    return out


def measure(vid, media_dir=MEDIA_DIR):
    """回傳這支影片每一句的切點失誤明細。"""
    srt = f"{media_dir}/{vid}.en.srt"
    mp4 = f"{media_dir}/{vid}.mp4"
    words = parse_json3(f"{media_dir}/{vid}.en.json3")
    sents = build_sentences(parse_srt(srt))

    rows, skipped = [], 0
    for s in sents:
        if not (BENCH_MIN_WORDS <= s["nwords"] <= BENCH_MAX_WORDS):
            continue
        found = unique_truth(words, s["text"], s["start"])
        if not found:
            skipped += 1
            continue
        i_first, i_last = found
        if i_last + 1 >= len(words):
            continue                       # 影片最後一句：沒有「下一個字」可比
        first_start = words[i_first][1]    # 實測值：句首第一個字開始發音的時間
        next_start = words[i_last + 1][1]  # 實測值：下一句第一個字開始發音的時間

        # 重現「沒有 json3」時的完整切檔路徑：內插估計 → 停頓對齊 → 頭尾餘裕
        est_start, est_end = s["start"], s["end"]
        snap_start, snap_end = snap_boundaries(mp4, est_start, est_end)
        cut_start = max(0.0, snap_start - SNAP_HEAD_PAD)
        cut_end = snap_end + SNAP_TAIL_PAD

        rows.append({
            "video": vid,
            "text": s["text"],
            "first_word_start": round(first_start, 3),
            "next_sentence_start": round(next_start, 3),
            "cut_start": round(cut_start, 3),
            "cut_end": round(cut_end, 3),
            # 切點晚於句首第一個字 → 開頭一定被切掉了（0 表示沒有這個問題）
            "head_missing": round(max(0.0, cut_start - first_start), 3),
            # 收點晚於下一句第一個字 → 一定吃到下一句了
            "tail_bleed": round(max(0.0, cut_end - next_start), 3),
            "snapped": snap_start != est_start or snap_end != est_end,
        })
    return rows, skipped


def _pct(vals, p):
    if not vals:
        return 0.0
    vals = sorted(vals)
    k = min(len(vals) - 1, int(round((p / 100) * (len(vals) - 1))))
    return vals[k]


def report(rows, skipped):
    if not rows:
        print("沒有可用的比對樣本。")
        return
    head = [r["head_missing"] for r in rows]
    tail = [r["tail_bleed"] for r in rows]
    n = len(rows)

    cand = n + skipped
    print(f"樣本數：{n} 句，來自 {len({r['video'] for r in rows})} 支影片")
    print(f"採用率：{n}/{cand} ({n/cand*100:.0f}%)　"
          f"其餘 {skipped} 句因字詞對不上或整句在片中重複出現、無法確定標準答案而排除")
    print(f"實際套用停頓對齊的比例：{sum(r['snapped'] for r in rows) / n * 100:.0f}%")
    print()
    bad_h = sum(1 for v in head if v > BAD_THRESHOLD)
    bad_t = sum(1 for v in tail if v > BAD_THRESHOLD)
    bad_any = sum(1 for r in rows
                  if r["head_missing"] > BAD_THRESHOLD or r["tail_bleed"] > BAD_THRESHOLD)

    print("兩個無法辯駁的失誤（都只用 json3 實測的「字開始時間」計算）")
    print(f"主要指標是「超過 {BAD_THRESHOLD}s（約一個英文單字）的比例」；"
          f"低於這個量級聽不出來，不列為瑕疵。\n")
    # 中位數/p90/最大都取全體樣本，不切換母體，避免出現「中位數大於 p90」這種
    # 看起來矛盾的並排（那是把超標子集的中位數跟全體的 p90 放在一起造成的）
    print(f"{'':26} {'超標比例':>9} {'中位數':>9} {'p90':>8} {'最大':>8}")
    for name, vals, bad in (("開頭被切掉（漏字）", head, bad_h),
                            ("結尾吃到下一句", tail, bad_t)):
        print(f"{name:26} {bad/n*100:>8.0f}% {statistics.median(vals):>9.3f} "
              f"{_pct(vals, 90):>8.3f} {max(vals):>8.3f}")
    print(f"\n任一端超標：{bad_any} 句 ({bad_any/n*100:.0f}%)")
    print()
    print("── 已知限制（解讀數字前必讀）" + "─" * 30)
    print("1. 這份數字是下限，不是實際狀況。取樣條件是「有 json3 逐字時間戳」，而這種")
    print("   影片的 SRT 與 json3 出自同一套自動聽打，時間軸天生就跟音訊一致。真正有")
    print("   問題的人工字幕影片還多了『字幕時間軸與音訊不同步』這個誤差源，本工具")
    print("   結構上量不到（見 issue #1）。")
    print("2. 標準答案要在 SRT 估計值前後 15 秒內找得到才算數，偏移超過這個範圍的句子")
    print("   會被排除而不是被計入——正好排除了最嚴重的失敗案例，同樣使數字偏樂觀。")
    print("3. 只計算「一定出錯」的量：切點晚於句首第一個字、或收點晚於下一句第一個字。")
    print("   落在中間的偏移（例如結尾多含了一段靜音）不算進來，因為 json3 沒有記錄")
    print("   字什麼時候結束，無法判定。")
    print("4. 被排除的句子不是隨機的：整句在片中重複出現的（多半較短、較口語）、以及")
    print("   字幕用字與自動聽打不一致的（常見於人工潤過的字幕）會被系統性排除，")
    print("   代表這份樣本略偏向「用字獨特、字幕與語音一致」的句子。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("videos", nargs="*", help="指定影片 ID；留空則跑全部")
    ap.add_argument("--json", help="把逐句明細另存成 JSON，供前後版本比較")
    ap.add_argument("--worst", type=int, default=5, help="列出失誤最大的前 N 句")
    args = ap.parse_args()

    vids = args.videos or video_ids()
    if not vids:
        sys.exit("找不到素材齊全的影片（需要 SRT + 含逐字時間戳的 json3 + mp4）。")

    rows, skipped = [], 0
    for i, vid in enumerate(vids, 1):
        print(f"[{i}/{len(vids)}] {vid} ...", flush=True)
        r, sk = measure(vid)
        rows.extend(r)
        skipped += sk

    print("\n" + "=" * 62)
    report(rows, skipped)

    if args.worst and rows:
        print(f"\n失誤最大的 {args.worst} 句：")
        worst = sorted(rows, key=lambda r: -max(r["head_missing"], r["tail_bleed"]))
        for r in worst[: args.worst]:
            print(f"  漏頭{r['head_missing']:.2f}s 超尾{r['tail_bleed']:.2f}s  "
                  f"{r['video']}  {r['text'][:52]}")

    if args.json:
        json.dump(rows, open(args.json, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print(f"\n明細已寫入 {args.json}")


if __name__ == "__main__":
    main()
