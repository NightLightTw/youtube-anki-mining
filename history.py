"""卡片修改歷史：記下管線「原本給了什麼」，稽核完再記「最後變成什麼」，長期追蹤管線品質。

為什麼需要：Anki 只存卡片現在的樣子，不存修改前的版本。沒有原始輸出，就無從知道
管線哪個欄位常被修、改一個規則到底有沒有讓人工修正變少。

兩份檔案（JSON Lines，只追加、不改寫），預設放在 repo 下的 history/（已 gitignore，
內容是使用者自己的例句，不該進公開 repo）；可用環境變數 YTM_HISTORY_DIR 改位置：

  created.jsonl    建卡當下的管線原始輸出，一張卡一行（mine.add_card 呼叫 record_created）
  finalized.jsonl  稽核完的最終狀態，一張卡一行；同一張卡可以再 finalize 多次
                   （例如事後補定義），統計時取每張卡最新的一筆

用法：
  python history.py finalize VIDEO_ID [--reason 文字]   # 稽核、同步完之後執行
  python history.py stats [--by video|pipeline|month]   # 看修正比例與趨勢
"""
import argparse
import base64
import datetime
import hashlib
import html
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

try:                                                   # POSIX 才有；沒有就不加鎖
    import fcntl
except ImportError:                                    # pragma: no cover
    fcntl = None

REPO = Path(__file__).resolve().parent
HISTORY_DIR = (Path(os.path.expanduser(os.environ["YTM_HISTORY_DIR"])).resolve()
               if os.environ.get("YTM_HISTORY_DIR") else REPO / "history")
SCHEMA = 1
TRACKED = ("Sentence", "Definition", "Chinese", "Synonyms", "Collocation")
LABELS = {"Sentence": "例句", "Definition": "定義", "Chinese": "中文", "Synonyms": "同義字", "Collocation": "搭配"}
UNCERTAIN_TAGS = ("pos-uncertain", "sense-uncertain", "no-definition", "definition-lookup-failed")


def _now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _git(*args):
    try:
        out = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True, timeout=5)
        return out.stdout.strip()
    except Exception:                                  # noqa: BLE001
        return ""


def pipeline_identity():
    """這張卡是哪一版程式產生的。只存版本與雜湊，不存 diff（diff 可能含私人內容）。

    光有 git HEAD 不夠：還沒提交的修改、同一個 HEAD 底下的實驗，會被算成同一版。
    所以另外存「有沒有未提交的修改」與「根目錄 *.py 的內容指紋」。"""
    head = _git("rev-parse", "HEAD")
    h = hashlib.sha256()
    for path in sorted(REPO.glob("*.py")):
        h.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    return dict(pipeline=head[:7] or None, pipeline_full=head or None,
                dirty=bool(_git("status", "--porcelain")), code_fingerprint=h.hexdigest()[:16])


def _append(name, record):
    """加鎖、只追加。上一次若寫到一半被中斷（檔尾沒有換行），先補上換行，
    新紀錄才不會接在殘缺的行後面；殘缺的行本身原封不動留著。

    flock 是額外的保險：開檔用 O_APPEND、整行一次 write，本機上本來就不容易被攪亂，
    而且這個保護沒有辦法用測試重現（拿掉鎖，並行寫入測試仍會通過）。"""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
    with open(HISTORY_DIR / name, "ab+") as f:
        if fcntl:
            fcntl.flock(f, fcntl.LOCK_EX)
        try:
            if f.seek(0, os.SEEK_END) > 0:
                f.seek(-1, os.SEEK_END)
                if f.read(1) != b"\n":
                    f.write(b"\n")
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        finally:
            if fcntl:
                fcntl.flock(f, fcntl.LOCK_UN)


def _read(name):
    """讀不懂的行（例如寫到一半被中斷）跳過並警告，不讓一行壞資料毀掉整份歷史。"""
    path = HISTORY_DIR / name
    if not path.exists():
        return []
    out = []
    # 只用 \n 分行：splitlines() 連 U+2028/U+2029 也會切，會把合法的 JSON 切成兩半
    for no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").split("\n"), 1):
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            print(f"  ⚠ {name} 第 {no} 行讀不懂，已跳過（原內容保留在檔案裡）", file=sys.stderr)
    return out


def norm(text):
    """HTML 欄位 → 純文字，用來判斷「內容有沒有變」；只改標籤或空白不算修改。"""
    # 換行與區塊標籤換成空白（a<br>b 與 a<div>b</div> 是同一件事），行內標籤（<b>）直接去掉
    text = re.sub(r"<br\s*/?>|</?(?:div|p|li|ul|ol|tr|td|table)\b[^>]*>", " ", text or "", flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return " ".join(html.unescape(text).split())


def classify(original, final):
    """unchanged（沒改）/ cleared（清空）/ filled（原本空、補上）/ changed（改成別的）"""
    o, f = norm(original), norm(final)
    if o == f:
        return "unchanged"
    if o and not f:
        return "cleared"
    if f and not o:
        return "filled"
    return "changed"


def file_md5(path):
    try:
        return hashlib.md5(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


# ---------- 建卡時 ----------

def record_created(note_id, note, video_id, word, surface, sent, audio_fn, audio_path, flags):
    """記下管線原始輸出。任何失敗都只警告：記錄歷史不能讓建卡失敗。"""
    try:
        _append("created.jsonl", dict(
            schema=SCHEMA, note_id=note_id, video_id=video_id, created_at=_now(),
            **pipeline_identity(), word=word, word_raw=note["fields"].get("Word", ""), surface=surface,
            fields={k: note["fields"].get(k, "") for k in TRACKED},
            tags=list(note["tags"]), flags=sorted(flags),
            audio=dict(filename=audio_fn, md5=file_md5(audio_path),
                       start=sent.get("start"), end=sent.get("end"),
                       from_json3=bool(sent.get("from_json3", False)))))
    except Exception as ex:                            # noqa: BLE001
        try:
            print(f"  ⚠ 修改歷史沒有記下來：{ex}", file=sys.stderr)
        except Exception:                              # noqa: BLE001
            pass


# ---------- 稽核完 ----------

def _media_md5(invoke, filename):
    data = invoke("retrieveMediaFile", filename=filename)
    if not data:
        return None
    return hashlib.md5(base64.b64decode(data)).hexdigest()


def audio_state(created, info, invoke):
    """卡片「現在」引用的音檔，跟建卡時比：
    unchanged 沒動 / replaced 同檔名但內容被換掉 / repointed 改指向別的檔 /
    missing_reference 欄位裡沒有音檔 / missing_file 引用的檔案不存在 / unknown 建卡時沒有指紋可比。
    內容變了只能說「檔案被換掉」，不能證明是重切（也可能是重新正規化音量）。"""
    base = created["audio"]
    m = re.search(r"\[sound:([^\]]+)\]", info["fields"].get("SentenceAudio", {}).get("value", ""))
    current = m.group(1) if m else None
    md5 = _media_md5(invoke, current) if current else None
    if not current:
        status = "missing_reference"
    elif md5 is None:
        status = "missing_file"
    elif not base.get("md5"):
        status = "unknown"
    elif current != base["filename"]:
        status = "repointed"
    elif md5 != base["md5"]:
        status = "replaced"
    else:
        status = "unchanged"
    return dict(filename=current, md5=md5, status=status, recut=status in ("replaced", "repointed"))


def build_final(created, info, invoke):
    """比對建卡原始輸出與 Anki 目前的內容；info 為 None 代表這張卡已被刪除。"""
    rec = dict(schema=SCHEMA, note_id=created["note_id"], video_id=created["video_id"],
               finalized_at=_now(), word=created["word"])
    if not info:
        return dict(rec, status="deleted")
    fields = {k: info["fields"].get(k, {}).get("value", "") for k in TRACKED}
    word_now = info["fields"].get("Word", {}).get("value", "")
    return dict(
        rec, status="kept", fields=fields, tags=list(info.get("tags", [])),
        edits={k: classify(created["fields"].get(k, ""), fields[k]) for k in TRACKED},
        word_changed=norm(word_now) != norm(created.get("word_raw", created["word"])),
        audio=audio_state(created, info, invoke))


def latest_created(video_id=None):
    """同一張卡有多筆建卡紀錄時取最後一筆；finalize 與統計共用，兩邊才不會算出不同的卡數。"""
    latest = {}
    for c in _read("created.jsonl"):
        if video_id is None or c["video_id"] == video_id:
            latest[c["note_id"]] = c
    return latest


def finalize(video_id, reason="audit", invoke=None):
    if invoke is None:
        from anki import invoke
    latest = latest_created(video_id)
    if not latest:
        raise SystemExit(f"history/created.jsonl 沒有影片 {video_id} 的建卡紀錄"
                         "（這支影片是在開始記錄歷史之前建的）")
    ids = list(latest)
    infos = invoke("notesInfo", notes=ids)
    if not isinstance(infos, list) or len(infos) != len(ids):
        raise SystemExit(f"notesInfo 回傳 {len(infos) if isinstance(infos, list) else '非清單'} 筆，"
                         f"預期 {len(ids)} 筆，不寫入以免把卡片配錯")
    by_id = {i["noteId"]: i for i in infos if i and i.get("noteId")}     # 已刪除的卡是空物件
    out = []
    for nid, c in latest.items():
        rec = build_final(c, by_id.get(nid), invoke)
        rec["reason"] = reason
        _append("finalized.jsonl", rec)
        out.append(rec)
    return out


# ---------- 統計 ----------

def _latest_final():
    final = {}
    for r in _read("finalized.jsonl"):
        final[r["note_id"]] = r                        # 後面的覆蓋前面的
    return final


def joined():
    """[(created, final)]，只含已經 finalize 過的卡。"""
    final = _latest_final()
    return [(c, final[nid]) for nid, c in latest_created().items() if nid in final]


def pipeline_label(created):
    """分組用的程式版本：git 版本 + 未提交標記(*) + 內容指紋。舊紀錄沒有指紋就只有版本。
    同一個 git 版本底下改了程式（沒提交）或做了實驗，指紋不同，不會被算成同一版。"""
    head = created.get("pipeline") or "?"
    mark = "*" if created.get("dirty") else ""
    fp = created.get("code_fingerprint")
    return f"{head}{mark}·{fp[:6]}" if fp else head


def _bucket(created, by):
    if by == "pipeline":
        return pipeline_label(created)
    if by == "month":
        return created["created_at"][:7]
    return created["video_id"]


def summarize(pairs, by="video"):
    groups = defaultdict(list)
    for c, f in pairs:
        groups[_bucket(c, by)].append((c, f))
    rows = []
    for key, items in sorted(groups.items()):
        kept = [(c, f) for c, f in items if f["status"] == "kept"]
        row = dict(key=key, created=len(items), kept=len(kept), deleted=len(items) - len(kept))
        for field in TRACKED:
            row[field] = Counter(f["edits"][field] for _, f in kept)
        row["audio"] = Counter(f["audio"].get("status", "unknown") for _, f in kept)   # 舊紀錄沒有狀態 = unknown
        rows.append(row)
    return rows


def flag_hit_rate(pairs):
    """標記命中率：建卡時被打上不確定標記、且最後留下來的卡，定義後來被改掉的比例，跟沒被標記的比較。
    被刪掉的卡不在分母裡。
    標記的用途是叫人去看；命中率高代表標記有抓到真問題。"""
    kept = [(c, f) for c, f in pairs if f["status"] == "kept"]
    out = {}
    for tag in UNCERTAIN_TAGS:
        flagged = [f for c, f in kept if tag in c["tags"]]
        out[tag] = (sum(f["edits"]["Definition"] != "unchanged" for f in flagged), len(flagged))
    plain = [f for c, f in kept if not any(t in c["tags"] for t in UNCERTAIN_TAGS)]
    out["（沒有標記）"] = (sum(f["edits"]["Definition"] != "unchanged" for f in plain), len(plain))
    return out


AUDIO_COMPARABLE = ("unchanged", "replaced", "repointed")


def audio_replaced_rate(counter):
    """(音檔被換過的張數, 能判斷的張數)。unknown／missing 不是「沒改」，不能放進分母。"""
    eligible = sum(counter[k] for k in AUDIO_COMPARABLE)
    return counter["replaced"] + counter["repointed"], eligible


def _pct(k, n):
    return f"{k / n:.0%}" if n else "-"


def print_stats(by="video"):
    pairs = joined()
    if not pairs:
        print("還沒有已 finalize 的資料。建卡後稽核完，執行：python history.py finalize VIDEO_ID")
        return
    rows = summarize(pairs, by)
    total = Counter()
    print(f"{'分組(' + by + ')':<16}{'建':>4}{'留':>4}{'刪':>4}  " +
          "".join(f"{LABELS[k]:>{9 - len(LABELS[k])}}" for k in TRACKED) + f"{'音檔換過':>{9 - 2}}")
    print("（欄位數字 = 留下的卡中內容被修改過的比例；音檔換過 = 能比對的卡中，音檔被換掉或改指向別檔的比例）")
    audio_total = Counter()
    for r in rows:
        cells = []
        for k in TRACKED:
            edited = r["kept"] - r[k]["unchanged"]
            total[k] += edited
            cells.append(f"{_pct(edited, r['kept']):>9}")
        total["kept"] += r["kept"]; total["created"] += r["created"]
        total["deleted"] += r["deleted"]
        audio_total.update(r["audio"])
        print(f"{r['key']:<16}{r['created']:>4}{r['kept']:>4}{r['deleted']:>4}  " + "".join(cells) +
              f"{_pct(*audio_replaced_rate(r['audio'])):>9}")
    print(f"{'合計':<16}{total['created']:>4}{total['kept']:>4}{total['deleted']:>4}  " +
          "".join(f"{_pct(total[k], total['kept']):>9}" for k in TRACKED) +
          f"{_pct(*audio_replaced_rate(audio_total)):>9}")
    print(f"刪除率 {_pct(total['deleted'], total['created'])}")
    print("音檔狀態：" + "、".join(f"{k} {v}" for k, v in sorted(audio_total.items())))
    print("\n不確定標記的命中率（定義被改掉的比例）：")
    for tag, (k, n) in flag_hit_rate(pairs).items():
        print(f"  {tag:<28}{k}/{n} = {_pct(k, n)}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("finalize")
    p.add_argument("video_id")
    p.add_argument("--reason", default="audit")
    p = sub.add_parser("stats")
    p.add_argument("--by", choices=("video", "pipeline", "month"), default="video")
    args = ap.parse_args(argv)
    if args.cmd == "finalize":
        recs = finalize(args.video_id, args.reason)
        kept = [r for r in recs if r["status"] == "kept"]
        edited = sum(any(v != "unchanged" for v in r["edits"].values()) for r in kept)
        print(f"✓ 已記錄 {args.video_id}：{len(recs)} 張，留下 {len(kept)}、刪除 {len(recs) - len(kept)}；"
              f"留下的卡中，例句／定義／中文／同義字／搭配有 {edited} 張被修改過")
    else:
        print_stats(args.by)


if __name__ == "__main__":
    main()
