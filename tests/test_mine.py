"""mine.py 純函式（字幕解析、句子重建、拼法備援）與 CLI 參數驗證的回歸測試。"""
import json
import sys

import pytest

import mine
from mine import _ise_to_ize, _ller_to_ler, build_sentences, parse_srt


# ---------- CLI 數值參數驗證（argparse error → SystemExit）----------

BAD_ARGV_CASES = [
    ["--auto", "--max-cards", "0", "--", "dummyid"],
    ["--auto", "--max-cards", "-3", "--", "dummyid"],
    ["--auto", "--min-words", "10", "--max-words", "5", "--", "dummyid"],
    ["--auto", "--min-words", "0", "--", "dummyid"],
    ["--auto", "--min-zipf", "5.0", "--max-zipf", "2.0", "--", "dummyid"],
    ["--auto", "--max-zipf", "nan", "--", "dummyid"],   # NaN 讓比較全為 False，須明確擋掉
    ["--auto", "--min-zipf", "nan", "--", "dummyid"],
    ["--index", "-1", "--word", "hello", "--", "dummyid"],
]


@pytest.mark.parametrize("argv", BAD_ARGV_CASES, ids=lambda a: " ".join(a[:-2]))
def test_invalid_numeric_args_are_rejected(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["mine.py"] + argv)
    with pytest.raises(SystemExit) as exc:
        mine.main()
    assert exc.value.code == 2  # argparse error 的退出碼；驗證發生在讀任何檔案之前


# ---------- 英式拼法備援（MW 查無 headword 時才會呼叫）----------

def test_ise_to_ize_converts_british_verbs():
    assert _ise_to_ize("prioritise") == "prioritize"
    assert _ise_to_ize("organise") == "organize"
    assert _ise_to_ize("recognise") == "recognize"


def test_ise_to_ize_length_guard():
    # 太短的字（rise/wise）不轉換，避免產生無意義變體
    assert _ise_to_ize("rise") is None
    assert _ise_to_ize("hello") is None


def test_ller_to_ler_converts_british_agent_nouns():
    assert _ller_to_ler("traveller") == "traveler"
    assert _ller_to_ler("counsellor") == "counselor"
    assert _ller_to_ler("jeweller") == "jeweler"


def test_ller_to_ler_length_guard_and_non_matches():
    assert _ller_to_ler("ller") is None
    assert _ller_to_ler("hello") is None


# ---------- SRT 解析與句子重建 ----------

SRT_SAMPLE = """\
1
00:00:00,000 --> 00:00:04,000
Hello world. This is

2
00:00:04,000 --> 00:00:08,000
a test sentence.

3
00:00:08,000 --> 00:00:10,000
>> [Music] Great stuff.
"""


def _write_srt(tmp_path, content=SRT_SAMPLE):
    p = tmp_path / "sample.en.srt"
    p.write_text(content, encoding="utf-8")
    return str(p)


def test_parse_srt_extracts_cues_and_strips_markers(tmp_path):
    cues = parse_srt(_write_srt(tmp_path))
    assert len(cues) == 3
    start, end, text = cues[0]
    assert (start, end) == (0.0, 4.0)
    assert text == "Hello world. This is"
    # 說話者標記 >> 與 [Music] 標籤要被清掉
    assert cues[2][2] == "Great stuff."


def test_build_sentences_joins_across_cues(tmp_path):
    sents = build_sentences(parse_srt(_write_srt(tmp_path)))
    texts = [s["text"] for s in sents]
    assert texts == ["Hello world.", "This is a test sentence.", "Great stuff."]


def test_build_sentences_times_are_sane(tmp_path):
    sents = build_sentences(parse_srt(_write_srt(tmp_path)))
    for s in sents:
        assert s["start"] < s["end"]
        assert s["nwords"] == len(s["text"].split())
    # 跨 cue 的句子：起點在第一個 cue 內、句首字的內插位置之後
    second = sents[1]
    assert 0.0 <= second["start"] < 8.0


def test_overlapping_cues_use_next_cue_start_as_span():
    """YouTube 滾動字幕的 cue 會重疊：cue 的結束時間是「這行從畫面消失」，
    不是「這行話講完」。內插必須改用下一個 cue 的開始當實際結束點，否則 cue 後段
    的字會被推到好幾秒之後（實測有 cue 標稱 6.4 秒、實際 1.9 秒就講完，導致
    句首被推遲 3.4 秒、切出來的音檔漏掉半句）。

    差異只在「句子從 cue 中間開始」時顯現——句子剛好在 cue 邊界開始的話，
    起點就是 cue 起點，兩種算法一樣。真實案例正是這種形態：
    cue 內容為 "...small business. What's"，下一句從 "What's" 起頭。
    """
    cues = [
        (0.0, 6.0, "aaa bbb. ccc ddd"),   # 標稱 6 秒，但下個 cue 2 秒就開始
        (2.0, 8.0, "eee fff."),
    ]
    sents = build_sentences(cues)
    assert sents[1]["text"].startswith("ccc")
    # 實際跨度 2 秒、四個字 → "ccc" 是第 3 個字，起點正好 2*2/4 = 1.0 秒。
    # 這是純算術、沒有音訊處理誤差，所以用精確值斷言。
    # 若誤用標稱的 6 秒跨度會算成 3.0 秒（晚兩秒，正是這個 bug 的症狀）。
    assert sents[1]["start"] == pytest.approx(1.0)


def test_non_overlapping_cues_behaviour_unchanged():
    """一般不重疊的字幕：下一個 cue 的開始 >= 本 cue 結束，取 min 後等同原本行為。"""
    cues = [
        (0.0, 4.0, "aaa bbb. ccc ddd"),   # 4 秒內講完，下個 cue 5 秒才開始
        (5.0, 8.0, "eee fff."),
    ]
    sents = build_sentences(cues)
    assert sents[1]["text"].startswith("ccc")
    # 跨度仍是完整的 4 秒、四個字各 1 秒 → "ccc" 起點正好 2.0 秒，不受修正影響
    assert sents[1]["start"] == pytest.approx(2.0)


def test_build_sentences_handles_no_trailing_punctuation():
    # 最後一句沒有句尾標點也要收尾，不能默默丟掉
    cues = [(0.0, 2.0, "An unfinished thought")]
    sents = build_sentences(cues)
    assert len(sents) == 1
    assert sents[0]["text"] == "An unfinished thought"


# ---------- 時間來源標記與靜音吸附的取捨 ----------
#
# 背景：句子時間有兩個來源，json3 逐字時間戳（準）與 SRT 線性內插（誤差可達數秒）。
# 靜音吸附是為了補救後者，但實測它會把已經夠準的 json3 邊界再推移 0.35~0.75 秒，
# 反而切掉句首或多帶進隔壁句。所以要能逐句分辨時間是哪裡來的。

def test_sentences_start_marked_as_not_from_json3():
    sents = build_sentences([(0.0, 2.0, "Hello world.")])
    assert sents[0]["from_json3"] is False


def _write_json3(tmp_path, words):
    """words: [(字, 起始秒)]，寫成 json3 的逐字格式。"""
    events = [{"tStartMs": int(s * 1000), "segs": [{"utf8": w, "tOffsetMs": 0}]}
              for w, s in words]
    p = tmp_path / "v.en.json3"
    p.write_text(json.dumps({"events": events}), encoding="utf-8")
    return str(p)


def test_refine_with_json3_marks_only_the_sentences_it_matched(tmp_path):
    """對得上的句子換成 json3 時間並標記，對不上的維持內插時間且不標記。"""
    cues = [(0.0, 3.0, "Alpha bravo charlie delta."), (3.0, 6.0, "Zulu yankee xray whiskey.")]
    sents = build_sentences(cues)
    # json3 只涵蓋第一句，且時間與內插值明顯不同
    j3 = _write_json3(tmp_path, [("Alpha", 10.0), ("bravo", 10.5), ("charlie", 11.0),
                                 ("delta.", 11.5)])
    n = mine.refine_with_json3(sents, j3)

    assert n == 1
    assert sents[0]["from_json3"] is True
    assert sents[0]["start"] == pytest.approx(10.0)
    assert sents[1]["from_json3"] is False          # 沒對到就不能標記
    assert sents[1]["start"] < 10.0                 # 時間也不該被動到


def test_refine_with_json3_missing_file_marks_nothing(tmp_path):
    sents = build_sentences([(0.0, 3.0, "Alpha bravo charlie delta.")])
    assert mine.refine_with_json3(sents, str(tmp_path / "nope.json3")) == 0
    assert sents[0]["from_json3"] is False


def test_extract_audio_pads_even_when_not_snapping(monkeypatch):
    """頭尾餘裕不能綁在吸附分支裡。

    這兩件事互相獨立：跳過吸附是因為 json3 的邊界已經夠準，不是因為不需要餘裕。
    早期版本把 pad 寫在 `if snap:` 裡面，跳過吸附就連帶失去餘裕，句首起音會被切掉。
    """
    calls = []
    monkeypatch.setattr(mine, "run", lambda cmd: calls.append(cmd))
    monkeypatch.setattr(mine, "snap_boundaries",
                        lambda *a, **k: pytest.fail("snap=False 不該呼叫吸附"))

    mine.extract_audio("v.mp4", 10.0, 12.0, "out.mp3", snap=False)

    cmd = calls[0]
    ss = float(cmd[cmd.index("-ss") + 1])
    dur = float(cmd[cmd.index("-t") + 1])
    assert ss == pytest.approx(10.0 - mine.SNAP_HEAD_PAD)
    assert dur == pytest.approx(2.0 + mine.SNAP_HEAD_PAD + mine.SNAP_TAIL_PAD)


def test_extract_audio_snaps_then_pads(monkeypatch):
    """snap=True 時先吸附、再對吸附後的邊界加同樣的餘裕。"""
    calls = []
    monkeypatch.setattr(mine, "run", lambda cmd: calls.append(cmd))
    monkeypatch.setattr(mine, "snap_boundaries", lambda v, s, e, **k: (20.0, 23.0))

    mine.extract_audio("v.mp4", 10.0, 12.0, "out.mp3", snap=True)

    cmd = calls[0]
    ss = float(cmd[cmd.index("-ss") + 1])
    dur = float(cmd[cmd.index("-t") + 1])
    assert ss == pytest.approx(20.0 - mine.SNAP_HEAD_PAD)
    assert dur == pytest.approx(3.0 + mine.SNAP_HEAD_PAD + mine.SNAP_TAIL_PAD)


def test_extract_audio_start_never_negative(monkeypatch):
    """句子從影片最開頭起算時，扣掉餘裕不能變成負數（ffmpeg -ss 會失敗）。"""
    calls = []
    monkeypatch.setattr(mine, "run", lambda cmd: calls.append(cmd))
    mine.extract_audio("v.mp4", 0.0, 2.0, "out.mp3", snap=False)
    assert float(calls[0][calls[0].index("-ss") + 1]) == pytest.approx(0.0)


def test_legacy_snap_forces_snapping_even_with_json3(monkeypatch, tmp_path):
    """--legacy-snap 是退路：樣本沒涵蓋到的素材若切壞了，要能退回舊行為。

    直接驗證 add_card 傳給 extract_audio 的 snap 參數，不碰 AnkiConnect。
    """
    seen = {}
    monkeypatch.setattr(mine, "invoke", lambda action, **kw: [True])
    monkeypatch.setattr(mine, "extract_audio",
                        lambda v, s, e, o, snap=True: seen.setdefault("snap", snap))
    monkeypatch.setattr(mine, "normalize_audio", lambda p: None)
    monkeypatch.setattr(mine, "store", lambda p, f: None)
    monkeypatch.setattr(mine, "fetch_definition", lambda *a, **k: "")
    monkeypatch.setattr(mine, "fetch_synonyms", lambda *a, **k: "")
    monkeypatch.setattr(mine, "fetch_chinese", lambda *a, **k: "")
    monkeypatch.setattr(mine, "MEDIA_DIR", str(tmp_path))

    sent = {"text": "Alpha bravo.", "start": 1.0, "end": 3.0, "nwords": 2, "from_json3": True}
    for legacy, expected in ((False, False), (True, True)):
        seen.clear()
        monkeypatch.setattr(mine, "invoke",
                            lambda action, **kw: [True] if action == "canAddNotes" else 1)
        mine.add_card("vid", "v.mp4", sent, "alpha", "t", legacy_snap=legacy)
        assert seen["snap"] is expected


def test_sentence_without_json3_still_snaps(monkeypatch, tmp_path):
    """沒有逐字時間戳（人工字幕）的句子維持吸附——那條路徑的行為不該被這次改動影響。"""
    seen = {}
    monkeypatch.setattr(mine, "invoke",
                        lambda action, **kw: [True] if action == "canAddNotes" else 1)
    monkeypatch.setattr(mine, "extract_audio",
                        lambda v, s, e, o, snap=True: seen.setdefault("snap", snap))
    monkeypatch.setattr(mine, "normalize_audio", lambda p: None)
    monkeypatch.setattr(mine, "store", lambda p, f: None)
    monkeypatch.setattr(mine, "fetch_definition", lambda *a, **k: "")
    monkeypatch.setattr(mine, "fetch_synonyms", lambda *a, **k: "")
    monkeypatch.setattr(mine, "fetch_chinese", lambda *a, **k: "")
    monkeypatch.setattr(mine, "MEDIA_DIR", str(tmp_path))

    sent = {"text": "Alpha bravo.", "start": 1.0, "end": 3.0, "nwords": 2, "from_json3": False}
    mine.add_card("vid", "v.mp4", sent, "alpha", "t")
    assert seen["snap"] is True
