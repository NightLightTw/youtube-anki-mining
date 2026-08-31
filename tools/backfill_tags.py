"""替既有卡片補上 pos-uncertain / sense-uncertain 標籤。

這些標籤是後來才加進管線的（見 mine.py 的 _entry_is_a_guess / _sense_is_a_guess），
先前建立的卡片沒有。這支工具重新計算旗標並補打標籤，**只加標籤、不動任何欄位**。

## 兩個關於準確度的前提，先講清楚

**一、跳過「現在的定義與管線選擇不符」的卡。** 這幾天有不少卡的定義是人工修正過的，
對那些卡重算旗標仍會說「管線當初是猜的」，但卡片現在是對的，再標只會製造假警報。
判斷方式是比對卡片現有的 Definition 與管線現在會挑的。**這不是可靠的「人工改過」
偵測**——字典內容更新、或詞義挑選演算法改版，都會讓沒被改過的卡看起來不一致。
所以這類一律跳過而不標記，寧可漏標也不誤標。

**二、字典查詢失敗絕不當成「沒問題」。** 這支工具刻意不用 mine.fetch_definition()，
因為它會把所有查詢例外吞掉並回傳空字串——那樣在 MW 每日額度用盡時，會把剩下的卡
全部標記成「已檢查、管線有把握」，而它們其實一張都沒被檢查過。這裡改為直接呼叫
_mw_lookup_with_fallback()，讓例外傳上來、停止並保留進度。

## 額度與續跑

MW 免費金鑰每天 1000 次查詢，牌組通常比這多：
  - 進度存在 --state 指定的檔案，中斷或額度用盡後再跑一次就會接續
  - --limit 控制單次跑幾張
  - --dry-run **不會**寫進度檔，可以放心先看統計

用法：
    python tools/backfill_tags.py --dry-run          # 只看會標多少，不動 Anki 也不記進度
    python tools/backfill_tags.py --limit 800        # 實際打標籤
    python tools/backfill_tags.py                    # 接續把剩下的跑完
"""
import argparse
import html
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mine
from anki import invoke

# 直接沿用管線的對照表，不自己維護一份。這裡先前是獨立的字典，管線多了 nodef
# 旗標之後這邊沒跟上，一跑就 KeyError——同一份資訊放兩個地方遲早會不同步。
TAG = mine.UNCERTAIN_TAG
BATCH = 50          # 每累積這麼多張就打一次標籤並存檔，減少 API 往返也限制中斷損失
DEFAULT_STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             ".backfill_tags_state.json")


def _plain(s):
    """去掉標籤與 HTML escape，只留可比對的文字。"""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip()


def load_state(path):
    try:
        return {"done": set(json.load(open(path, encoding="utf-8"))["done"])}
    except Exception:
        return {"done": set()}


def save_state(path, done):
    """原子寫入：先寫暫存檔再改名，避免中途斷電留下半個檔案而讓進度歸零。"""
    tmp = path + ".tmp"
    json.dump({"done": sorted(done)}, open(tmp, "w", encoding="utf-8"))
    os.replace(tmp, path)


def analyse(word, sentence, surface):
    """重算這張卡的旗標，並回傳管線現在會挑的定義。

    刻意不走 fetch_definition()——它會吞掉查詢例外回傳空字串，那會讓額度用盡與
    「這個字查無定義」變得無法區分。這裡讓例外往上拋。
    """
    found = mine._mw_lookup_with_fallback("learners", mine.MW_LEARNERS_KEY, word)
    homographs = found.homographs
    if not homographs:
        # 可能是衍生詞（掛在母詞的 uros 底下），管線現在會用母詞定義救回
        if mine._find_run_on(found.raw, word):
            return frozenset(), ""      # 救得回來，不算不確定
        return frozenset({"nodef"}), ""  # 字典真的沒有這個字（查詢本身成功了）
    wanted_pos = mine._guess_pos(surface or word, sentence) if sentence else None
    flags = set()
    if mine._entry_is_a_guess(homographs, wanted_pos):
        flags.add("pos")
    entry = next((e for e in homographs if e.get("fl") == wanted_pos), homographs[0])
    shortdefs = entry.get("shortdef") or []
    if not shortdefs:
        return frozenset(flags), ""
    if mine._sense_is_a_guess(shortdefs, sentence, surface):
        flags.add("sense")
    idx = mine._pick_sense(shortdefs, sentence, surface)
    definition = f"<i>{html.escape(entry.get('fl',''))}</i> {mine._mw_clean(shortdefs[idx])}"
    return frozenset(flags), definition


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只統計，不打標籤也不寫進度檔")
    ap.add_argument("--limit", type=int, default=0, help="這次最多處理幾張（0=不限）")
    ap.add_argument("--state", default=DEFAULT_STATE, help="進度檔路徑")
    ap.add_argument("--deck", default=mine.DECK_NAME, help="只處理這個牌組")
    args = ap.parse_args()

    if not mine.MW_LEARNERS_KEY:
        sys.exit("需要 MW_LEARNERS_KEY（在 .env 設定），這支工具要重查字典")

    done = load_state(args.state)["done"] if not args.dry_run else set()
    query = f'"note:{mine.MODEL_NAME}" "deck:{args.deck}"'
    note_ids = invoke("findNotes", query=query)
    todo = [n for n in note_ids if n not in done]
    print(f"牌組「{args.deck}」共 {len(note_ids)} 張；已處理 {len(note_ids)-len(todo)} 張，"
          f"待處理 {len(todo)} 張")
    if args.limit:
        todo = todo[:args.limit]
        print(f"（--limit {args.limit}，本次只跑 {len(todo)} 張）")
    if not todo:
        print("沒有待處理的卡片。")
        return

    # 本次執行的統計（不跨次累積，免得看不出這一趟做了什麼）
    n_tagged = n_clean = n_mismatch = n_already = 0
    hits = {"pos": [], "sense": []}
    pending = {}                 # {標籤組合: [note_id]}，累積到 BATCH 再一次打
    staged = []                  # 已算完、等標籤寫入後才計入 done 的 id
    stopped = None

    def flush():
        nonlocal pending, staged
        if not args.dry_run:
            for tags, ids in pending.items():
                if ids:
                    invoke("addTags", notes=ids, tags=tags)
            if staged:
                done.update(staged)
                save_state(args.state, done)
        pending, staged = {}, []

    try:
        for i, nid in enumerate(todo, 1):
            info = invoke("notesInfo", notes=[nid])[0]
            f = {k: v["value"] for k, v in info["fields"].items()}
            word, sent_html = f.get("Word", ""), f.get("Sentence", "")
            sentence = _plain(sent_html)
            if not word or not sentence:
                staged.append(nid)
                continue
            m = re.search(r"<b>(.*?)</b>", sent_html)      # 建卡時被 highlight 的就是 surface
            surface = _plain(m.group(1)) if m else _plain(word)

            existing = set(info.get("tags") or [])
            try:
                flags, pipeline_def = analyse(word, sentence, surface)
            except Exception as ex:
                stopped = f"{word}：{ex}"
                break

            # 內容對不上管線現在的選擇——可能被人工改過，也可能只是字典更新了。
            # 兩者分不出來，所以一律跳過不標，寧可漏標也不要誤標。
            if pipeline_def and _plain(f.get("Definition")) != _plain(pipeline_def):
                n_mismatch += 1
                staged.append(nid)
                continue

            want = {TAG[k] for k in flags} - existing      # 只補還沒有的那個
            if flags and not want:
                n_already += 1
            elif want:
                for k in flags:
                    if TAG[k] in want:
                        hits[k].append(word)
                pending.setdefault(" ".join(sorted(want)), []).append(nid)
                n_tagged += 1
            else:
                n_clean += 1
            staged.append(nid)
            if len(staged) >= BATCH:
                flush()
                print(f"  …{i}/{len(todo)}（本次已標 {n_tagged}）", flush=True)
            time.sleep(0.05)
    finally:
        flush()

    print(f"\n{'（乾跑：沒有打標籤，也沒有寫進度檔）' if args.dry_run else '本次完成'}")
    print(f"  新打標籤            {n_tagged} 張")
    for k in ("pos", "sense"):
        if hits[k]:
            more = " …" if len(hits[k]) > 12 else ""
            print(f"    {TAG[k]:16s} {len(hits[k]):>4d} 張  {'、'.join(hits[k][:12])}{more}")
    print(f"  管線有把握，未標    {n_clean} 張")
    print(f"  已經有標籤          {n_already} 張")
    print(f"  與管線選擇不符      {n_mismatch} 張（可能已人工修正，跳過不標）")
    if stopped:
        print(f"\n⚠ 字典查詢失敗，已停止：{stopped}")
        print("  進度已保存（失敗的那張沒有記為已處理），稍後再跑一次就會接續。")
        print("  若是 MW 每日額度用盡，等隔天再跑。")
    elif not args.dry_run:
        print(f"\n進度檔：{args.state}（全部跑完後可以刪掉）")


if __name__ == "__main__":
    main()
