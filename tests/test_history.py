"""history.py：建卡時記下管線原始輸出，稽核完比對最終狀態，統計修改比例。"""
import base64
import hashlib
import json

import pytest

import history
import mine


@pytest.fixture(autouse=True)
def _history_enabled(monkeypatch):
    """這個檔案的測試預設是「已啟用」（維護者的情境）；關閉的行為在檔案最後明確測。"""
    monkeypatch.setenv("RECORD_HISTORY", "1")


# ---------- 修改分類 ----------

@pytest.mark.parametrize("original,final,expected", [
    ("a gadget", "a gadget", "unchanged"),
    ("a gadget", "a tool", "changed"),
    ("a gadget", "", "cleared"),
    ("", "a tool", "filled"),
    ("", "", "unchanged"),
    # 只動標籤、實體、空白不算修改
    ("It&#x27;s <b>big</b>", "It's big", "unchanged"),
    ("a   b", "a b", "unchanged"),
    ("x<br>y", "x y", "unchanged"),
])
def test_classify(original, final, expected):
    assert history.classify(original, final) == expected


def test_classify_treats_a_field_with_only_markup_left_as_cleared():
    # 清空後只剩標籤 = 清空，而不是「改成另一個東西」
    assert history.classify("synonyms", "<i></i>") == "cleared"


def test_block_tags_keep_word_boundaries():
    # a<br>b 與 a<div>b</div> 是同一件事；但 <b> 這種行內標籤不能把字黏起來以外的東西搞混
    assert history.classify("a<br>b", "a<div>b</div>") == "unchanged"
    assert history.classify("ab", "a<br>b") == "changed"


# ---------- 建卡時的記錄 ----------

def _note(definition="verb to push", synonyms="≈ shove", tags=("youtube-mining", "vid1")):
    return {"fields": {"Sentence": "He <b>thrust</b> it.", "Definition": definition, "Chinese": "推",
                       "Collocation": "", "Synonyms": synonyms, "Word": "thrust"},
            "tags": list(tags)}


def _created(tmp_path, note_id=1, **kw):
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"original-audio")
    history.record_created(note_id, _note(**kw), "vid1", "thrust", "thrust",
                           {"start": 1.0, "end": 2.5, "from_json3": True},
                           "a.mp3", str(audio), frozenset({"sense"}))
    return audio


def test_record_created_keeps_original_pipeline_output(tmp_path):
    _created(tmp_path)
    (rec,) = history._read("created.jsonl")
    assert rec["note_id"] == 1 and rec["video_id"] == "vid1" and rec["word"] == "thrust"
    assert rec["fields"]["Definition"] == "verb to push" and rec["fields"]["Synonyms"] == "≈ shove"
    assert rec["flags"] == ["sense"] and "vid1" in rec["tags"]
    assert rec["audio"] == {"filename": "a.mp3", "md5": hashlib.md5(b"original-audio").hexdigest(),
                            "start": 1.0, "end": 2.5, "from_json3": True}


def test_recording_failure_never_breaks_card_creation(tmp_path, monkeypatch, capsys):
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(history, "_append", boom)
    history.record_created(1, _note(), "vid1", "thrust", "thrust", {"start": 0, "end": 1},
                           "a.mp3", str(tmp_path / "missing.mp3"), frozenset())
    assert "沒有記下來" in capsys.readouterr().err


def test_missing_audio_file_is_recorded_without_md5(tmp_path):
    history.record_created(1, _note(), "vid1", "thrust", "thrust", {"start": 0, "end": 1},
                           "a.mp3", str(tmp_path / "nope.mp3"), frozenset())
    assert history._read("created.jsonl")[0]["audio"]["md5"] is None


def test_add_card_writes_a_history_record(monkeypatch, tmp_path):
    """整合：add_card 建卡後真的呼叫 record_created，帶著管線原始輸出。"""
    monkeypatch.setattr(mine, "invoke", lambda action, **kw:
                        [{"canAdd": True}] if action == "canAddNotesWithErrorDetail" else 4242)
    monkeypatch.setattr(mine, "extract_audio", lambda *a, **k: None)
    monkeypatch.setattr(mine, "normalize_audio", lambda p: None)
    monkeypatch.setattr(mine, "store", lambda p, f: None)
    monkeypatch.setattr(mine, "fetch_definition", lambda *a, **k: "verb to push")
    monkeypatch.setattr(mine, "fetch_synonyms", lambda *a, **k: "≈ shove")
    monkeypatch.setattr(mine, "fetch_chinese", lambda *a, **k: "推")
    monkeypatch.setattr(mine, "MEDIA_DIR", str(tmp_path))
    sent = {"text": "He thrust it.", "start": 1.0, "end": 2.0, "nwords": 3, "from_json3": True}
    assert mine.add_card("vid1", "v.mp4", sent, "thrust", "t") == 4242
    (rec,) = history._read("created.jsonl")
    assert rec["note_id"] == 4242 and rec["fields"]["Definition"] == "verb to push"
    assert rec["audio"]["from_json3"] is True


# ---------- 稽核完 ----------

class FakeAnki:
    def __init__(self, notes, media=None):
        self.notes, self.media = notes, media or {}

    def __call__(self, action, **kw):
        if action == "notesInfo":
            return [self.notes.get(i, {}) for i in kw["notes"]]   # 已刪除的卡回空物件
        if action == "retrieveMediaFile":
            data = self.media.get(kw["filename"])
            return base64.b64encode(data).decode() if data is not None else False
        raise AssertionError(action)


def _info(definition, synonyms, tags, sentence="He <b>thrust</b> it.", word="thrust", chinese="推",
          nid=1, audio="[sound:a.mp3]"):
    return {"noteId": nid, "tags": tags,
            "fields": {k: {"value": v} for k, v in dict(
                Sentence=sentence, Definition=definition, Chinese=chinese, Collocation="",
                Synonyms=synonyms, Word=word, SentenceAudio=audio).items()}}


def test_finalize_records_each_kind_of_edit(tmp_path):
    _created(tmp_path)
    fake = FakeAnki({1: _info("noun a forward push", "", ["vid1", "youtube-mining"])},
                    media={"a.mp3": b"original-audio"})
    (rec,) = history.finalize("vid1", invoke=fake)
    assert rec["status"] == "kept"
    assert rec["edits"] == {"Sentence": "unchanged", "Definition": "changed", "Chinese": "unchanged",
                            "Synonyms": "cleared", "Collocation": "unchanged"}
    assert rec["audio"]["recut"] is False and rec["word_changed"] is False


def test_finalize_detects_recut_audio(tmp_path):
    _created(tmp_path)
    fake = FakeAnki({1: _info("verb to push", "≈ shove", [])}, media={"a.mp3": b"recut-audio"})
    assert history.finalize("vid1", invoke=fake)[0]["audio"]["recut"] is True


def test_finalize_marks_a_missing_note_as_deleted(tmp_path):
    _created(tmp_path)
    (rec,) = history.finalize("vid1", invoke=FakeAnki({}))
    assert rec["status"] == "deleted" and "edits" not in rec


def test_finalize_notices_when_the_target_word_itself_was_changed(tmp_path):
    _created(tmp_path)
    fake = FakeAnki({1: _info("x", "", [], word="thrust forward")}, media={"a.mp3": b"original-audio"})
    assert history.finalize("vid1", invoke=fake)[0]["word_changed"] is True


def test_finalize_refuses_a_video_with_no_creation_record(tmp_path):
    with pytest.raises(SystemExit, match="沒有影片 nope 的建卡紀錄"):
        history.finalize("nope", invoke=FakeAnki({}))


def test_refinalizing_keeps_history_and_uses_the_latest(tmp_path):
    _created(tmp_path)
    media = {"a.mp3": b"original-audio"}
    history.finalize("vid1", reason="audit", invoke=FakeAnki({1: _info("verb to push", "≈ shove", [])}, media))
    history.finalize("vid1", reason="backfill",
                     invoke=FakeAnki({1: _info("verb to shove", "≈ shove", [])}, media))
    assert len(history._read("finalized.jsonl")) == 2                  # 兩次都留著
    ((_, final),) = history.joined()
    assert final["reason"] == "backfill" and final["edits"]["Definition"] == "changed"


# ---------- 統計 ----------

def _two_cards(tmp_path):
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    for nid, vid in ((1, "v1"), (2, "v1"), (3, "v2")):
        history.record_created(nid, _note(tags=("youtube-mining", vid, "sense-uncertain") if nid == 1
                                          else ("youtube-mining", vid)),
                               vid, "w", "w", {"start": 0, "end": 1}, "a.mp3", str(audio), frozenset())
    media = {"a.mp3": b"x"}
    unchanged = _info("verb to push", "≈ shove", [])
    fake = FakeAnki({1: dict(unchanged, noteId=1,
                             fields=dict(unchanged["fields"], Definition={"value": "something else"})),
                     2: dict(unchanged, noteId=2)}, media)            # 3 號被刪
    history.finalize("v1", invoke=fake)
    history.finalize("v2", invoke=fake)


def test_summary_counts_created_kept_deleted_and_edits(tmp_path):
    _two_cards(tmp_path)
    rows = {r["key"]: r for r in history.summarize(history.joined(), "video")}
    assert (rows["v1"]["created"], rows["v1"]["kept"], rows["v1"]["deleted"]) == (2, 2, 0)
    assert rows["v1"]["Definition"] == {"changed": 1, "unchanged": 1}
    assert (rows["v2"]["created"], rows["v2"]["kept"], rows["v2"]["deleted"]) == (1, 0, 1)


def test_flag_hit_rate_compares_flagged_and_unflagged_cards(tmp_path):
    _two_cards(tmp_path)
    hits = history.flag_hit_rate(history.joined())
    assert hits["sense-uncertain"] == (1, 1)          # 被標記的那張，定義後來真的被改了
    assert hits["（沒有標記）"] == (0, 1)               # 沒標記的那張沒被改


def test_unfinalized_videos_are_not_counted(tmp_path):
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    history.record_created(9, _note(), "v9", "w", "w", {"start": 0, "end": 1}, "a.mp3", str(audio), frozenset())
    assert history.joined() == []


def test_stats_output_when_nothing_recorded(capsys):
    history.print_stats()
    assert "還沒有已 finalize 的資料" in capsys.readouterr().out


def test_stats_output_mentions_totals_and_flag_rates(tmp_path, capsys):
    _two_cards(tmp_path)
    history.print_stats("video")
    out = capsys.readouterr().out
    assert "合計" in out and "刪除率 33%" in out and "sense-uncertain" in out
    assert "音檔狀態：" in out


# ---------- codex 審查後補的案例 ----------

def test_audio_status_covers_every_outcome(tmp_path):
    _created(tmp_path)
    created = history.latest_created()[1]
    media = {"a.mp3": b"original-audio", "b.mp3": b"other"}

    def status(audio_field, media=media):
        info = _info("d", "s", [], audio=audio_field)
        return history.audio_state(created, info, FakeAnki({}, media))["status"]

    assert status("[sound:a.mp3]") == "unchanged"
    assert status("[sound:a.mp3]", {"a.mp3": b"new"}) == "replaced"
    assert status("[sound:b.mp3]") == "repointed"          # 改指向新檔，舊檔沒動
    assert status("") == "missing_reference"
    assert status("[sound:gone.mp3]") == "missing_file"


def test_audio_status_is_unknown_when_creation_had_no_fingerprint(tmp_path):
    history.record_created(1, _note(), "vid1", "thrust", "thrust", {"start": 0, "end": 1},
                           "a.mp3", str(tmp_path / "nope.mp3"), frozenset())     # 建卡時檔案不在
    created = history.latest_created()[1]
    got = history.audio_state(created, _info("d", "s", []), FakeAnki({}, {"a.mp3": b"x"}))
    assert got["status"] == "unknown" and got["recut"] is False    # 無法判斷 ≠ 沒改


def test_repointed_audio_counts_as_recut_in_the_final_record(tmp_path):
    _created(tmp_path)
    fake = FakeAnki({1: _info("verb to push", "≈ shove", [], audio="[sound:new.mp3]")},
                    {"a.mp3": b"original-audio", "new.mp3": b"fresh"})
    audio = history.finalize("vid1", invoke=fake)[0]["audio"]
    assert audio["recut"] is True and audio["filename"] == "new.mp3"


def test_truncated_last_line_does_not_destroy_the_history(tmp_path, capsys):
    _created(tmp_path, note_id=1)
    path = history.HISTORY_DIR / "created.jsonl"
    with open(path, "ab") as f:
        f.write(b'{"schema": 1, "note_id": 2, "vid')           # 寫到一半被中斷
    _created(tmp_path, note_id=3)
    ids = [r["note_id"] for r in history._read("created.jsonl")]
    assert ids == [1, 3]                                       # 新紀錄沒有接在殘缺的行後面
    assert b'"note_id": 2, "vid' in path.read_bytes()           # 壞掉的行原封不動留著
    assert "第 2 行讀不懂" in capsys.readouterr().err


def test_duplicate_creation_records_are_counted_once(tmp_path):
    _created(tmp_path, note_id=1)
    _created(tmp_path, note_id=1)                              # 同一張卡被記錄兩次
    history.finalize("vid1", invoke=FakeAnki({}))              # 這張卡已被刪除
    rows = history.summarize(history.joined(), "video")
    assert (rows[0]["created"], rows[0]["deleted"]) == (1, 1)


def test_notesinfo_is_paired_by_note_id_with_a_missing_note_in_between(tmp_path):
    for nid in (1, 2, 3):
        _created(tmp_path, note_id=nid)
    fake = FakeAnki({1: _info("a", "", [], nid=1), 3: _info("c", "", [], nid=3)},
                    {"a.mp3": b"original-audio"})
    got = {r["note_id"]: r["status"] for r in history.finalize("vid1", invoke=fake)}
    assert got == {1: "kept", 2: "deleted", 3: "kept"}


def test_finalize_refuses_a_short_notesinfo_response(tmp_path):
    _created(tmp_path, note_id=1)
    _created(tmp_path, note_id=2)
    with pytest.raises(SystemExit, match="預期 2 筆"):
        history.finalize("vid1", invoke=lambda action, **kw: [_info("a", "", [])])
    assert history._read("finalized.jsonl") == []              # 寧可不寫，也不要配錯


def test_record_is_written_even_if_the_deck_move_fails(monkeypatch, tmp_path):
    """addNote 成功但 findCards/changeDeck 失敗：卡已經存在，原始輸出不能遺失。"""
    def fake(action, **kw):
        if action == "canAddNotesWithErrorDetail":
            return [{"canAdd": True}]
        if action == "addNote":
            return 77
        raise RuntimeError("Search snapshot capacity exceeded")
    monkeypatch.setattr(mine, "invoke", fake)
    monkeypatch.setattr(mine, "extract_audio", lambda *a, **k: None)
    monkeypatch.setattr(mine, "normalize_audio", lambda p: None)
    monkeypatch.setattr(mine, "store", lambda p, f: None)
    monkeypatch.setattr(mine, "fetch_definition", lambda *a, **k: "verb to push")
    monkeypatch.setattr(mine, "fetch_synonyms", lambda *a, **k: "")
    monkeypatch.setattr(mine, "fetch_chinese", lambda *a, **k: "推")
    monkeypatch.setattr(mine, "MEDIA_DIR", str(tmp_path))
    with pytest.raises(RuntimeError):
        mine.add_card("vid1", "v.mp4", {"text": "He thrust it.", "start": 0.0, "end": 1.0, "nwords": 3},
                      "thrust", "t")
    assert [r["note_id"] for r in history._read("created.jsonl")] == [77]


def test_pipeline_identity_is_recorded_without_any_diff_content(tmp_path):
    _created(tmp_path)
    rec = history._read("created.jsonl")[0]
    assert set(("pipeline", "pipeline_full", "dirty", "code_fingerprint")) <= set(rec)
    assert len(rec["code_fingerprint"]) == 16 and isinstance(rec["dirty"], bool)
    assert "diff" not in json.dumps(rec).lower()


def test_code_fingerprint_changes_when_code_changes(monkeypatch, tmp_path):
    before = history.pipeline_identity()["code_fingerprint"]
    fake_repo = tmp_path / "repo"
    fake_repo.mkdir()
    (fake_repo / "mine.py").write_text("x = 1")
    monkeypatch.setattr(history, "REPO", fake_repo)
    a = history.pipeline_identity()["code_fingerprint"]
    (fake_repo / "mine.py").write_text("x = 2")
    b = history.pipeline_identity()["code_fingerprint"]
    assert a != b and a != before


def test_word_change_compares_against_the_original_word_field(tmp_path):
    _created(tmp_path)
    fake = FakeAnki({1: _info("d", "", [], word="thrust")}, {"a.mp3": b"original-audio"})
    assert history.finalize("vid1", invoke=fake)[0]["word_changed"] is False


def test_history_dir_env_var_is_expanded(monkeypatch, tmp_path):
    import importlib
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("YTM_HISTORY_DIR", "~/h")
    try:
        assert importlib.reload(history).HISTORY_DIR == (tmp_path / "h").resolve()
    finally:
        monkeypatch.delenv("YTM_HISTORY_DIR")
        importlib.reload(history)


def test_cli_finalize_and_stats_are_wired_up(tmp_path, capsys, monkeypatch):
    _created(tmp_path)
    fake = FakeAnki({1: _info("verb to push", "≈ shove", [])}, {"a.mp3": b"original-audio"})
    import anki
    monkeypatch.setattr(anki, "invoke", fake)
    history.main(["finalize", "vid1", "--reason", "cli"])
    history.main(["stats", "--by", "pipeline"])
    out = capsys.readouterr().out
    assert "已記錄 vid1：1 張，留下 1、刪除 0" in out and "分組(pipeline)" in out
    assert history._read("finalized.jsonl")[0]["reason"] == "cli"


def test_history_dir_is_really_ignored_by_git():
    import pathlib
    import subprocess
    repo = pathlib.Path(__file__).resolve().parent.parent
    r = subprocess.run(["git", "-C", str(repo), "check-ignore", "history/created.jsonl"],
                       capture_output=True, text=True)
    assert r.returncode == 0, "history/ 沒有被 .gitignore 擋下，使用者的例句會進版控"


def test_notesinfo_in_a_different_order_is_still_paired_by_note_id(tmp_path):
    """AnkiConnect 不保證回傳順序跟請求順序一致；配錯會把 A 卡的修改算到 B 卡。"""
    for nid in (1, 2):
        _created(tmp_path, note_id=nid)
    infos = [_info("def of two", "", [], nid=2), _info("def of one", "", [], nid=1)]   # 順序顛倒
    calls = []

    def fake(action, **kw):
        if action == "notesInfo":
            return infos
        calls.append(action)
        return base64.b64encode(b"original-audio").decode()

    got = {r["note_id"]: r["fields"]["Definition"] for r in history.finalize("vid1", invoke=fake)}
    assert got == {1: "def of one", 2: "def of two"}


def test_concurrent_writers_never_interleave_lines(tmp_path):
    """三個行程同時追加大筆資料：每一行都要是完整的 JSON，一筆都不能少。

    注意這只驗證結果。實測拿掉 flock 這個測試仍會通過：O_APPEND 加單次 write 在本機上
    本來就不容易被攪亂，flock 是額外的保險（跨平台、大於一個磁碟區塊的寫入），
    沒有辦法用測試重現它擋下的競爭。"""
    import subprocess
    import sys
    import textwrap
    code = textwrap.dedent("""
        import sys, pathlib
        sys.path.insert(0, sys.argv[1])
        import history
        history.HISTORY_DIR = pathlib.Path(sys.argv[2])
        for i in range(150):
            history._append("c.jsonl", {"w": sys.argv[3], "i": i, "pad": "x" * 6000})
    """)
    repo = str(__import__("pathlib").Path(history.__file__).parent)
    procs = [subprocess.Popen([sys.executable, "-c", code, repo, str(history.HISTORY_DIR), tag])
             for tag in ("a", "b", "c")]
    assert all(p.wait(timeout=60) == 0 for p in procs)
    rows = history._read("c.jsonl")                      # 有任何一行被攪在一起就會讀不懂而少一筆
    assert len(rows) == 450 and len({(r["w"], r["i"]) for r in rows}) == 450


# ---------- 第二輪審查後補的案例 ----------

def test_pipeline_grouping_separates_different_code_under_the_same_head(tmp_path):
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    for nid, fp, dirty in ((1, "aaaaaa1111111111", False), (2, "bbbbbb2222222222", False),
                           (3, "bbbbbb2222222222", True)):
        history.record_created(nid, _note(), "v1", "w", "w", {"start": 0, "end": 1}, "a.mp3", str(audio),
                               frozenset())
    path = history.HISTORY_DIR / "created.jsonl"
    rows = [json.loads(l) for l in path.read_text().splitlines()]
    for r, fp, dirty in zip(rows, ("aaaaaa1111111111", "bbbbbb2222222222", "bbbbbb2222222222"),
                            (False, False, True)):
        r.update(pipeline="abc1234", code_fingerprint=fp, dirty=dirty)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    fake = FakeAnki({n: _info("d", "", [], nid=n) for n in (1, 2, 3)}, {"a.mp3": b"x"})
    history.finalize("v1", invoke=fake)
    keys = [r["key"] for r in history.summarize(history.joined(), "pipeline")]
    assert keys == ["abc1234*·bbbbbb", "abc1234·aaaaaa", "abc1234·bbbbbb"]


def test_legacy_record_without_fingerprint_still_groups_by_head():
    assert history.pipeline_label({"pipeline": "abc1234"}) == "abc1234"
    assert history.pipeline_label({}) == "?"


def test_audio_rate_excludes_unknown_and_missing_from_the_denominator():
    from collections import Counter
    c = Counter(unchanged=6, replaced=2, repointed=2, unknown=50, missing_file=5)
    assert history.audio_replaced_rate(c) == (4, 10)      # 4/10，不是 4/65
    assert history.audio_replaced_rate(Counter(unknown=3)) == (0, 0)


def test_summary_reports_audio_statuses_and_legacy_records_count_as_unknown(tmp_path):
    _created(tmp_path)
    media = {"a.mp3": b"original-audio"}
    history.finalize("vid1", invoke=FakeAnki({1: _info("verb to push", "≈ shove", [])}, media))
    path = history.HISTORY_DIR / "finalized.jsonl"
    rec = json.loads(path.read_text())
    del rec["audio"]["status"]                              # 舊格式：沒有狀態欄位
    path.write_text(json.dumps(rec) + "\n")
    (row,) = history.summarize(history.joined(), "video")
    assert row["audio"] == {"unknown": 1}


def test_records_with_unicode_line_separators_survive_a_round_trip():
    # ensure_ascii=False 會原樣寫出 U+2028；str.splitlines() 會把一筆合法紀錄切成兩半
    history._append("c.jsonl", {"note_id": 1, "text": "a\u2028b\u2029c\x85d\x0be"})
    history._append("c.jsonl", {"note_id": 2})
    rows = history._read("c.jsonl")
    assert [r["note_id"] for r in rows] == [1, 2]
    assert rows[0]["text"] == "a\u2028b\u2029c\x85d\x0be"


def test_append_works_without_fcntl(monkeypatch):
    """Windows 沒有 fcntl：不加鎖也要能寫（import mine 不能因此失敗）。"""
    monkeypatch.setattr(history, "fcntl", None)
    history._append("c.jsonl", {"ok": True})
    assert history._read("c.jsonl") == [{"ok": True}]


def test_flag_hit_rate_excludes_deleted_cards(tmp_path):
    _two_cards(tmp_path)                                   # 1 號有標記且留下、3 號被刪
    assert history.flag_hit_rate(history.joined())["sense-uncertain"] == (1, 1)


# ---------- 預設關閉（一般使用者不該多出一個資料夾） ----------

def test_recording_is_off_unless_explicitly_enabled(monkeypatch, tmp_path, capsys):
    """沒啟用時完全不動作：不建資料夾、不算指紋、不呼叫 git、不印任何東西。"""
    monkeypatch.delenv("RECORD_HISTORY")
    monkeypatch.setattr(history, "pipeline_identity", lambda: (_ for _ in ()).throw(AssertionError("不該被呼叫")))
    monkeypatch.setattr(history, "file_md5", lambda p: (_ for _ in ()).throw(AssertionError("不該被呼叫")))
    _created(tmp_path)
    out = capsys.readouterr()
    assert not history.HISTORY_DIR.exists() and out.out == "" and out.err == ""


@pytest.mark.parametrize("value", ["", "0", "true", "yes", "on"])
def test_only_the_exact_value_1_enables_recording(monkeypatch, tmp_path, value, capsys):
    monkeypatch.setenv("RECORD_HISTORY", value)
    monkeypatch.setattr(history, "pipeline_identity", lambda: (_ for _ in ()).throw(AssertionError("不該被呼叫")))
    _created(tmp_path)
    out = capsys.readouterr()
    assert not history.HISTORY_DIR.exists() and out.out == "" and out.err == ""


def test_add_card_leaves_no_trace_when_history_is_off(monkeypatch, tmp_path):
    monkeypatch.delenv("RECORD_HISTORY")
    monkeypatch.setattr(mine, "invoke", lambda action, **kw:
                        [{"canAdd": True}] if action == "canAddNotesWithErrorDetail" else 5)
    monkeypatch.setattr(mine, "extract_audio", lambda *a, **k: None)
    monkeypatch.setattr(mine, "normalize_audio", lambda p: None)
    monkeypatch.setattr(mine, "store", lambda p, f: None)
    monkeypatch.setattr(mine, "fetch_definition", lambda *a, **k: "d")
    monkeypatch.setattr(mine, "fetch_synonyms", lambda *a, **k: "")
    monkeypatch.setattr(mine, "fetch_chinese", lambda *a, **k: "")
    monkeypatch.setattr(mine, "MEDIA_DIR", str(tmp_path))
    calls = []
    monkeypatch.setattr(history.subprocess, "run", lambda *a, **k: calls.append(a))   # 連 git 都不該呼叫
    assert mine.add_card("v", "v.mp4", {"text": "A b.", "start": 0.0, "end": 1.0, "nwords": 2}, "b", "t") == 5
    assert not history.HISTORY_DIR.exists() and calls == []


def test_disabled_history_tells_the_user_how_to_turn_it_on(monkeypatch, capsys):
    monkeypatch.delenv("RECORD_HISTORY")
    history.print_stats()
    assert "RECORD_HISTORY=1" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="RECORD_HISTORY=1"):
        history.finalize("v", invoke=FakeAnki({}))


def test_enabled_history_does_not_nag_in_the_empty_stats_message(capsys):
    history.print_stats()
    assert "RECORD_HISTORY=1" not in capsys.readouterr().out


def test_standalone_cli_sees_record_history_in_dotenv(monkeypatch, tmp_path, capsys):
    """單獨執行 history.py 不經過 mine.py：.env 裡已經啟用的人，不該再被提示「請啟用」。"""
    monkeypatch.delenv("RECORD_HISTORY")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".env").write_text("MW_LEARNERS_KEY=secret\nRECORD_HISTORY=1\n")
    monkeypatch.setattr(history, "REPO", repo)
    history.main(["stats"])
    out = capsys.readouterr().out
    assert "RECORD_HISTORY=1" not in out and "還沒有已 finalize 的資料" in out


def test_standalone_cli_still_hints_when_dotenv_has_no_flag(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("RECORD_HISTORY")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".env").write_text("MW_LEARNERS_KEY=secret\n")
    monkeypatch.setattr(history, "REPO", repo)
    history.main(["stats"])
    assert "RECORD_HISTORY=1" in capsys.readouterr().out
