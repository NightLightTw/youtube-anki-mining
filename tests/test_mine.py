"""mine.py 純函式（字幕解析、句子重建、拼法備援）與 CLI 參數驗證的回歸測試。"""
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
    # 實際跨度 2 秒、四個字 → "ccc" 是第 3 個字，起點應在 1.0 秒附近。
    # 若誤用標稱的 6 秒跨度，會算成 3.0 秒（晚兩秒，正是這個 bug 的症狀）。
    assert 0.9 <= sents[1]["start"] <= 1.1


def test_non_overlapping_cues_behaviour_unchanged():
    """一般不重疊的字幕：下一個 cue 的開始 >= 本 cue 結束，取 min 後等同原本行為。"""
    cues = [
        (0.0, 4.0, "aaa bbb. ccc ddd"),   # 4 秒內講完，下個 cue 5 秒才開始
        (5.0, 8.0, "eee fff."),
    ]
    sents = build_sentences(cues)
    assert sents[1]["text"].startswith("ccc")
    # 跨度仍是完整的 4 秒、四個字各 1 秒 → "ccc" 起點 2.0 秒，不受修正影響
    assert 1.9 <= sents[1]["start"] <= 2.1


def test_build_sentences_handles_no_trailing_punctuation():
    # 最後一句沒有句尾標點也要收尾，不能默默丟掉
    cues = [(0.0, 2.0, "An unfinished thought")]
    sents = build_sentences(cues)
    assert len(sents) == 1
    assert sents[0]["text"] == "An unfinished thought"
