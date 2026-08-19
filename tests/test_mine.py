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


def test_build_sentences_handles_no_trailing_punctuation():
    # 最後一句沒有句尾標點也要收尾，不能默默丟掉
    cues = [(0.0, 2.0, "An unfinished thought")]
    sents = build_sentences(cues)
    assert len(sents) == 1
    assert sents[0]["text"] == "An unfinished thought"
