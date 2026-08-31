"""mine.py 純函式（字幕解析、句子重建、拼法備援）與 CLI 參數驗證的回歸測試。"""
import json
import sys
import urllib.error

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


# ---------- 詞義選擇沒有依據時的提示 ----------

def test_sense_guess_flagged_when_nothing_overlaps():
    """多個候選字義、但例句跟哪一個都對不上——這時挑第一個純粹是猜的。"""
    shortdefs = ["to surround (an area) with a hedge",
                 "to avoid giving a promise or direct answer"]
    sent = "I'm going to hedge and say I'm not sure."
    assert mine._sense_is_a_guess(shortdefs, sent, "hedge") is True


def test_sense_not_flagged_when_something_overlaps():
    """有依據就不提示，免得訊號被雜訊淹掉。"""
    shortdefs = ["a thick, flat piece of meat and especially beef",
                 "a thick, flat piece of fish"]
    sent = "I ordered the fish but they brought me a steak."
    assert mine._sense_is_a_guess(shortdefs, sent, "steak") is False


def test_single_sense_never_flagged():
    """只有一個字義就沒得挑，不算猜測。"""
    assert mine._sense_is_a_guess(["the only meaning"], "A sentence here.", "x") is False


def test_sense_guess_needs_a_sentence():
    """手動模式可能沒有例句可比對，那時不該亂報。"""
    assert mine._sense_is_a_guess(["one", "two"], "", "x") is False


def test_pick_sense_and_guess_flag_stay_consistent():
    """兩者共用同一組分數：只要有任一義項對得上，就該挑那個且不標記；
    全部對不上則退回第一義且標記。這個一致性是提示有意義的前提。"""
    sds = ["a thick, flat piece of meat and especially beef", "a thick, flat piece of fish"]
    hit = "I ordered the fish but they brought me this."
    miss = "Hold on, let me check that again."
    assert mine._pick_sense(sds, hit, "steak") == 1
    assert mine._sense_is_a_guess(sds, hit, "steak") is False
    assert mine._pick_sense(sds, miss, "steak") == 0
    assert mine._sense_is_a_guess(sds, miss, "steak") is True


def _mw_entry(shortdefs, fl="noun", hw="widget"):
    return [{"hwi": {"hw": hw}, "fl": fl, "shortdef": shortdefs}]


def test_fetch_definition_records_the_word_when_it_had_to_guess(monkeypatch):
    monkeypatch.setattr(mine, "MW_LEARNERS_KEY", "dummy")
    monkeypatch.setattr(mine, "_mw_get",
                        lambda ref, key, w: _mw_entry(["a small gadget", "a whatsit"]))
    out = mine.fetch_definition("widget", sentence="Hold on, let me check that again.",
                                surface="widget")
    assert "a small gadget" in out                 # 仍然退回第一義，行為不變
    assert "sense" in mine.LAST_FLAGS              # 但標記出來了


def test_fetch_definition_records_nothing_when_it_had_evidence(monkeypatch):
    monkeypatch.setattr(mine, "MW_LEARNERS_KEY", "dummy")
    monkeypatch.setattr(mine, "_mw_get",
                        lambda ref, key, w: _mw_entry(["a small gadget", "a kind of fish"]))
    out = mine.fetch_definition("widget", sentence="They served us fish for dinner.",
                                surface="widget")
    assert "fish" in out
    assert mine.LAST_FLAGS == set()


def test_fetch_definition_records_nothing_for_single_sense(monkeypatch):
    monkeypatch.setattr(mine, "MW_LEARNERS_KEY", "dummy")
    monkeypatch.setattr(mine, "_mw_get", lambda ref, key, w: _mw_entry(["the only meaning"]))
    mine.fetch_definition("widget", sentence="Hold on, let me check that.", surface="widget")
    assert mine.LAST_FLAGS == set()


def test_fetch_definition_records_nothing_without_api_key(monkeypatch):
    monkeypatch.setattr(mine, "MW_LEARNERS_KEY", "")
    assert mine.fetch_definition("widget", sentence="Anything at all.") == ""
    assert mine.LAST_FLAGS == set()


# ---------- 翻譯端點的備援 ----------
#
# 中文欄靠的是 Google 翻譯網頁版的內部端點（非官方、無保證）。網址裡的 client
# 參數決定 Google 套用哪套配額：實測 2026-08 起 client=gtx 一律回 429，且與來源
# IP、User-Agent 都無關——是那個 client 整個被限制。所以要能換下一個繼續試。

def test_translate_falls_back_to_the_next_client(monkeypatch):
    tried = []

    def fake_get(url, **kw):
        tried.append("dict-chrome-ex" if "dict-chrome-ex" in url else "gtx")
        if tried[-1] == "dict-chrome-ex":
            raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)
        return [[["你好", "hello", None, None, 3]]]

    monkeypatch.setattr(mine, "_http_get_json", fake_get)
    monkeypatch.setattr(mine, "_TRANSLATE_CLIENTS", ("dict-chrome-ex", "gtx"))
    assert mine._google_translate("hello") == "你好"
    assert tried == ["dict-chrome-ex", "gtx"]      # 第一個失敗才試第二個


def test_translate_stops_at_the_first_working_client(monkeypatch):
    """第一個就通的話不該再打第二次——多餘的請求只會加速被限流。"""
    tried = []

    def fake_get(url, **kw):
        tried.append(url)
        return [[["你好", "hello", None, None, 3]]]

    monkeypatch.setattr(mine, "_http_get_json", fake_get)
    assert mine._google_translate("hello") == "你好"
    assert len(tried) == 1


def test_translate_raises_when_every_client_fails(monkeypatch):
    """全部都不通時要拋出來，讓上層記錄失敗、把中文欄留空，而不是回傳空字串假裝成功。"""
    def fake_get(url, **kw):
        raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)

    monkeypatch.setattr(mine, "_http_get_json", fake_get)
    with pytest.raises(urllib.error.HTTPError):
        mine._google_translate("hello")


def test_translate_joins_multi_segment_responses(monkeypatch):
    """長句會被切成多段回傳，要接回去；空段落要略過。"""
    monkeypatch.setattr(mine, "_http_get_json",
                        lambda url, **kw: [[["前半段", "a", None], [None, "b"], ["後半段", "c"]]])
    assert mine._google_translate("whatever") == "前半段後半段"


def test_translate_does_not_switch_client_on_transient_errors(monkeypatch):
    """逾時／連線重置這類暫時性錯誤不該換 client。

    _http_get_json 內部本來就會重試；這裡再換一個 client 等於同一次故障打兩倍的
    請求，而反覆請求正是最容易讓端點把你限流的事。
    """
    tried = []

    def fake_get(url, **kw):
        tried.append(url)
        raise TimeoutError("connection timed out")

    monkeypatch.setattr(mine, "_http_get_json", fake_get)
    with pytest.raises(TimeoutError):
        mine._google_translate("hello")
    assert len(tried) == 1          # 只打了第一個，沒有擴散


def test_translate_keeps_the_first_failure_for_diagnosis(monkeypatch):
    """全部失敗時回報第一個 client 的錯誤——後面那個是已知會壞的備援，
    拿它的錯誤去查問題只會誤導。"""
    def fake_get(url, **kw):
        code = 503 if "dict-chrome-ex" in url else 429
        raise urllib.error.HTTPError(url, code, "boom", {}, None)

    monkeypatch.setattr(mine, "_http_get_json", fake_get)
    with pytest.raises(urllib.error.HTTPError) as exc:
        mine._google_translate("hello")
    assert exc.value.code == 503


def test_translate_does_not_hide_a_malformed_response(monkeypatch):
    """回傳格式不如預期是程式問題，要炸出來，不能被當成『換下一個 client』吞掉。"""
    monkeypatch.setattr(mine, "_http_get_json", lambda url, **kw: {"unexpected": "shape"})
    with pytest.raises((KeyError, TypeError, IndexError)):
        mine._google_translate("hello")


# ---------- 詞條層（挑錯詞性）的偵測 ----------
#
# 這比義項挑錯嚴重：整條定義的詞性都不對。實例 fare——例句是 "train fares"（名詞
# 票價），但字典第一個詞條是動詞，卡片就寫成 "to do something well or badly"。

def test_entry_guess_flagged_when_pos_unknown_and_multiple_pos():
    hs = [{"fl": "verb"}, {"fl": "noun"}]
    assert mine._entry_is_a_guess(hs, None) is True


def test_entry_not_flagged_when_pos_was_determined():
    """猜得出詞性就是有依據的選擇，不算盲選。"""
    hs = [{"fl": "verb"}, {"fl": "noun"}]
    assert mine._entry_is_a_guess(hs, "noun") is False


def test_entry_not_flagged_when_only_one_pos():
    """只有一種詞性就沒得挑，退回第一個不是猜。"""
    assert mine._entry_is_a_guess([{"fl": "noun"}, {"fl": "noun"}], None) is False


def test_fetch_definition_sets_pos_flag(monkeypatch):
    monkeypatch.setattr(mine, "MW_LEARNERS_KEY", "dummy")
    monkeypatch.setattr(mine, "_mw_get", lambda ref, key, w: [
        {"hwi": {"hw": "fare"}, "fl": "verb", "shortdef": ["to do something well or badly"]},
        {"hwi": {"hw": "fare"}, "fl": "noun", "shortdef": ["the money a person pays to travel"]},
    ])
    monkeypatch.setattr(mine, "_guess_pos", lambda *a: None)
    out = mine.fetch_definition("fare", sentence="They're saying train fares might go up.",
                                surface="fares")
    assert "well or badly" in out              # 行為不變，仍退回第一個詞條
    assert "pos" in mine.LAST_FLAGS            # 但標記出來了


def test_add_card_tags_carry_the_uncertainty(monkeypatch, tmp_path):
    """旗標要變成 note 的 tag——卡面不動，之後在 Anki 用 tag 篩出來複查。"""
    seen = {}

    def fake_invoke(action, **kw):
        if action == "canAddNotes":
            return [True]
        if action == "addNote":
            seen["tags"] = kw["note"]["tags"]
            return 1
        return None

    monkeypatch.setattr(mine, "invoke", fake_invoke)
    monkeypatch.setattr(mine, "extract_audio", lambda *a, **k: None)
    monkeypatch.setattr(mine, "normalize_audio", lambda p: None)
    monkeypatch.setattr(mine, "store", lambda p, f: None)
    monkeypatch.setattr(mine, "fetch_synonyms", lambda *a, **k: "")
    monkeypatch.setattr(mine, "fetch_chinese", lambda *a, **k: "")
    monkeypatch.setattr(mine, "MEDIA_DIR", str(tmp_path))

    def fake_def(word, sentence="", surface=""):
        mine.LAST_FLAGS.clear()
        mine.LAST_FLAGS.update({"pos", "sense"})
        return "x"

    monkeypatch.setattr(mine, "fetch_definition", fake_def)
    sent = {"text": "Alpha bravo.", "start": 1.0, "end": 3.0, "nwords": 2, "from_json3": True}
    mine.add_card("vid", "v.mp4", sent, "alpha", "t")
    assert seen["tags"] == ["youtube-mining", "vid", "pos-uncertain", "sense-uncertain"]


def test_add_card_has_no_uncertainty_tags_when_confident(monkeypatch, tmp_path):
    seen = {}

    def fake_invoke(action, **kw):
        if action == "canAddNotes":
            return [True]
        if action == "addNote":
            seen["tags"] = kw["note"]["tags"]
            return 1

    monkeypatch.setattr(mine, "invoke", fake_invoke)
    monkeypatch.setattr(mine, "extract_audio", lambda *a, **k: None)
    monkeypatch.setattr(mine, "normalize_audio", lambda p: None)
    monkeypatch.setattr(mine, "store", lambda p, f: None)
    monkeypatch.setattr(mine, "fetch_synonyms", lambda *a, **k: "")
    monkeypatch.setattr(mine, "fetch_chinese", lambda *a, **k: "")
    monkeypatch.setattr(mine, "MEDIA_DIR", str(tmp_path))
    monkeypatch.setattr(mine, "fetch_definition",
                        lambda *a, **k: (mine.LAST_FLAGS.clear(), "x")[1])
    sent = {"text": "Alpha bravo.", "start": 1.0, "end": 3.0, "nwords": 2, "from_json3": True}
    mine.add_card("vid", "v.mp4", sent, "alpha", "t")
    assert seen["tags"] == ["youtube-mining", "vid"]


def test_entry_flagged_when_guessed_pos_absent_from_dictionary():
    """猜出了詞性、但字典沒收那個詞性——next() 一樣靜靜退回第一個詞條，也是盲選。

    這條在早期版本漏掉了：當時只檢查 `wanted_pos is None`。
    """
    hs = [{"fl": "noun"}, {"fl": "adjective"}]
    assert mine._entry_is_a_guess(hs, "verb") is True
    assert mine._entry_is_a_guess(hs, "noun") is False     # 字典有這個詞性就不是猜


def test_early_returns_do_not_leak_previous_flags(monkeypatch):
    """每條提早結束的路徑都必須把旗標清乾淨，否則上一張卡的不確定性會被貼到下一張。"""
    monkeypatch.setattr(mine, "MW_LEARNERS_KEY", "")
    mine.LAST_FLAGS.update({"pos", "sense"})           # 假裝上一次留下的
    mine.fetch_definition("widget", sentence="Anything.")
    assert mine.LAST_FLAGS == set()

    monkeypatch.setattr(mine, "MW_LEARNERS_KEY", "dummy")
    monkeypatch.setattr(mine, "_mw_get", lambda ref, key, w: [])   # 查無詞條
    mine.LAST_FLAGS.update({"pos"})
    mine.fetch_definition("widget", sentence="Anything.")
    # 查無詞條會設 nodef，但上一次殘留的 pos 必須被清掉
    assert mine.LAST_FLAGS == {"nodef"}

    def boom(ref, key, w):
        raise RuntimeError("network down")
    monkeypatch.setattr(mine, "_mw_get", boom)          # 例外路徑
    mine.LAST_FLAGS.update({"sense"})
    assert mine.fetch_definition("widget", sentence="Anything.") == ""
    # 查詢失敗會標 lookupfail，但上一次殘留的 sense 必須被清掉
    assert mine.LAST_FLAGS == {"lookupfail"}


def test_uncertain_summary_only_records_cards_that_were_created(monkeypatch, tmp_path):
    """建卡失敗的字不該出現在最後的複查清單裡——那張卡根本不存在。"""
    monkeypatch.setattr(mine, "extract_audio", lambda *a, **k: None)
    monkeypatch.setattr(mine, "normalize_audio", lambda p: None)
    monkeypatch.setattr(mine, "store", lambda p, f: None)
    monkeypatch.setattr(mine, "fetch_synonyms", lambda *a, **k: "")
    monkeypatch.setattr(mine, "fetch_chinese", lambda *a, **k: "")
    monkeypatch.setattr(mine, "MEDIA_DIR", str(tmp_path))
    monkeypatch.setattr(mine, "fetch_definition",
                        lambda *a, **k: (mine.LAST_FLAGS.clear(),
                                         mine.LAST_FLAGS.add("pos"), "x")[2])

    def failing_invoke(action, **kw):
        if action == "canAddNotes":
            return [True]
        raise RuntimeError("AnkiConnect 掛了")

    monkeypatch.setattr(mine, "invoke", failing_invoke)
    mine.UNCERTAIN.clear()
    sent = {"text": "Alpha bravo.", "start": 1.0, "end": 3.0, "nwords": 2, "from_json3": True}
    with pytest.raises(RuntimeError):
        mine.add_card("vid", "v.mp4", sent, "alpha", "t")
    assert mine.UNCERTAIN == []


def test_uncertain_summary_records_created_cards(monkeypatch, tmp_path):
    monkeypatch.setattr(mine, "extract_audio", lambda *a, **k: None)
    monkeypatch.setattr(mine, "normalize_audio", lambda p: None)
    monkeypatch.setattr(mine, "store", lambda p, f: None)
    monkeypatch.setattr(mine, "fetch_synonyms", lambda *a, **k: "")
    monkeypatch.setattr(mine, "fetch_chinese", lambda *a, **k: "")
    monkeypatch.setattr(mine, "MEDIA_DIR", str(tmp_path))
    monkeypatch.setattr(mine, "fetch_definition",
                        lambda *a, **k: (mine.LAST_FLAGS.clear(),
                                         mine.LAST_FLAGS.add("sense"), "x")[2])
    monkeypatch.setattr(mine, "invoke",
                        lambda action, **kw: [True] if action == "canAddNotes" else 1)
    mine.UNCERTAIN.clear()
    sent = {"text": "Alpha bravo.", "start": 1.0, "end": 3.0, "nwords": 2, "from_json3": True}
    mine.add_card("vid", "v.mp4", sent, "alpha", "t")
    assert mine.UNCERTAIN == [("alpha", frozenset({"sense"}))]


# ---------- 字幕裡的非語音標記 ----------
#
# TED 之類的人工字幕會用 (Laughter) 標註現場聲響。那些會被寫進卡片例句，但音檔裡
# 沒有這段聲音，複習時就變成「字幕有、聽不到」。實測一支 TED 影片有 23 處。

def test_nonspeech_markers_are_stripped(tmp_path):
    p = tmp_path / "v.en.srt"
    p.write_text(
        "1\n00:00:01,000 --> 00:00:04,000\n(Laughter) You don't need to be an expert.\n\n"
        "2\n00:00:05,000 --> 00:00:08,000\nIt works. (Applause)\n\n"
        "3\n00:00:09,000 --> 00:00:12,000\nShe said it (very quietly) to me.\n",
        encoding="utf-8")
    cues = parse_srt(str(p))
    assert cues[0][2] == "You don't need to be an expert."
    assert cues[1][2] == "It works."
    # 一般括號是正文的一部分，不能一起清掉
    assert cues[2][2] == "She said it (very quietly) to me."


@pytest.mark.parametrize("marker", [
    # 掃過 repo 內全部字幕實際出現的形態，用詞比想像中多樣
    "(Laughter)", "(Applause)", "(Cheers and applause)", "(Clears throat)",
    "(Imitates barking)", "(Snaps her fingers)", "(With the audience)",
    "(Video starts)", "(Soothing music)", "(Crack)", "(Sigh)",
    "(laughter)", "(applause)",   # W3C 轉錄指引建議非語音標記用小寫
    "(LAUGHTER)",
])
def test_real_world_nonspeech_markers_are_stripped(tmp_path, marker):
    p = tmp_path / "v.en.srt"
    p.write_text(f"1\n00:00:01,000 --> 00:00:04,000\n{marker} Hello there.\n", encoding="utf-8")
    assert parse_srt(str(p))[0][2] == "Hello there."


@pytest.mark.parametrize("kept", [
    "(very quietly)",   # 補充說明
    "(I think)",
    "(see chapter 3)",
    "(2019)",
    "(New York)",       # 地名——早期版本用「大寫開頭的短句」當規則時會被誤刪
    "(John Doe)",       # 人名，同上
])
def test_ordinary_parentheses_are_kept(tmp_path, kept):
    p = tmp_path / "v.en.srt"
    p.write_text(f"1\n00:00:01,000 --> 00:00:04,000\nShe said it {kept} to me.\n", encoding="utf-8")
    assert parse_srt(str(p))[0][2] == f"She said it {kept} to me."


# ---------- 派生詞（uros）救回 ----------
#
# MW 把 -ly 副詞、-ity 名詞這類「意思可從母詞推得」的衍生詞掛在母詞條目底下，
# 只給詞性和例句、不給定義。查 humbly 時 MW 回的是 humble 的條目，headword 比對
# 不上就整條丟掉、Definition 欄留空。實測一支 TED 影片 20 張卡有 6 張空白，
# 其中 3 張是這個原因。

def _entry_with_uro(hw, fl, shortdefs, ure, ure_fl):
    return [{"hwi": {"hw": hw}, "fl": fl, "shortdef": shortdefs,
             "uros": [{"ure": ure, "fl": ure_fl}]}]


def test_run_on_derivative_is_found():
    data = _entry_with_uro("hum*ble", "adjective", ["not proud"], "hum*bly", "adverb")
    assert mine._find_run_on(data, "humbly") == ("humble", "adverb", ["not proud"])


def test_run_on_lookup_ignores_unrelated_entries():
    data = _entry_with_uro("hum*ble", "adjective", ["not proud"], "hum*bly", "adverb")
    assert mine._find_run_on(data, "something-else") is None


def test_fetch_definition_recovers_derivative_and_marks_its_origin(monkeypatch):
    """定義只能用母詞的，所以要標明來源——否則會出現詞性與內容打架：
    vulnerability 標 noun，定義文字卻是形容詞的 "easily hurt or harmed"。"""
    monkeypatch.setattr(mine, "MW_LEARNERS_KEY", "dummy")
    monkeypatch.setattr(mine, "_mw_get", lambda ref, key, w: _entry_with_uro(
        "vul*ner*a*ble", "adjective", ["easily hurt or harmed"],
        "vul*ner*a*bil*i*ty", "noun"))
    out = mine.fetch_definition("vulnerability", sentence="This requires vulnerability.",
                                surface="vulnerability")
    assert "<i>noun</i>" in out              # 詞性用衍生詞自己的
    assert "vulnerable" in out               # 但標明定義是誰的
    assert "easily hurt or harmed" in out
    assert "nodef" not in mine.LAST_FLAGS    # 救回來了就不算查無


def test_genuinely_missing_word_is_flagged(monkeypatch):
    """字典真的沒收、也不是任何詞條的衍生詞——Definition 會是空的，要留下痕跡。"""
    monkeypatch.setattr(mine, "MW_LEARNERS_KEY", "dummy")
    monkeypatch.setattr(mine, "_mw_get", lambda ref, key, w: [])
    assert mine.fetch_definition("aneurysm", sentence="She had an aneurysm.",
                                 surface="aneurysm") == ""
    assert "nodef" in mine.LAST_FLAGS


def test_missing_api_key_is_not_flagged_as_missing_definition(monkeypatch):
    """沒設金鑰是使用者的設定問題，不是這個字查不到，不該打 no-definition。"""
    monkeypatch.setattr(mine, "MW_LEARNERS_KEY", "")
    assert mine.fetch_definition("anything", sentence="A sentence.") == ""
    assert mine.LAST_FLAGS == set()


def test_lookup_result_is_accessed_by_name(monkeypatch):
    """查詢結果用具名欄位存取，不靠位置解包。

    這個函式先前從回傳 2 個值改成 3 個，散在各處的位置解包沒有全部跟上——
    tools/backfill_tags.py 漏改了，一跑就 ValueError，而當時的測試沒抓到。
    """
    monkeypatch.setattr(mine, "_mw_get", lambda ref, key, w: [
        {"hwi": {"hw": "happy"}, "fl": "adjective", "shortdef": ["feeling pleasure"]}])
    r = mine._mw_lookup_with_fallback("learners", "dummy", "happy")
    assert r.homographs and r.matched_word == "happy" and r.raw


def test_backfill_tool_works_with_the_lookup_result(monkeypatch):
    """repo 內另一個呼叫端：確保它跟著新的回傳形狀走。"""
    import importlib.util, pathlib
    spec = importlib.util.spec_from_file_location(
        "backfill_tags", pathlib.Path(__file__).parent.parent / "tools" / "backfill_tags.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mine, "MW_LEARNERS_KEY", "dummy")
    monkeypatch.setattr(mine, "_mw_get", lambda ref, key, w: [
        {"hwi": {"hw": "widget"}, "fl": "noun", "shortdef": ["a small gadget", "a whatsit"]}])
    flags, definition = mod.analyse("widget", "Hold on, let me check that.", "widget")
    assert "a small gadget" in definition
    assert "sense" in flags


def test_synonyms_path_unpacks_correctly(monkeypatch):
    """實際走一次 fetch_synonyms，確認解包不會炸。"""
    monkeypatch.setattr(mine, "MW_THESAURUS_KEY", "dummy")
    monkeypatch.setattr(mine, "_mw_get", lambda ref, key, w: [
        {"meta": {"id": "happy", "syns": [["glad", "joyful"]], "ants": [["sad"]]},
         "fl": "adjective", "shortdef": ["feeling pleasure"]}])
    out = mine.fetch_synonyms("happy", sentence="I am happy today.", surface="happy")
    assert "glad" in out


# ---------- 衍生詞救回的界線 ----------
#
# 母詞定義並非總能套到衍生詞上。界線是「會讀錯的擋掉，只是需要轉換的保留」。

def _uro_entry(hw, base_fl, shortdefs, ure, ure_fl):
    return [{"hwi": {"hw": hw}, "fl": base_fl, "shortdef": shortdefs,
             "uros": [{"ure": ure, "fl": ure_fl}]}]


@pytest.mark.parametrize("hw,base_fl,ure,ure_fl", [
    ("hum*ble", "adjective", "hum*bly", "adverb"),           # 形容詞→副詞，直接套用就通
    ("vul*ner*a*ble", "adjective", "vulnerability", "noun"), # 形容詞→名詞
    ("ev*o*lu*tion", "noun", "evolutionary", "adjective"),   # 名詞→形容詞，讀者自行轉換
])
def test_transferable_relations_are_recovered(hw, base_fl, ure, ure_fl):
    data = _uro_entry(hw, base_fl, ["some definition"], ure, ure_fl)
    assert mine._find_run_on(data, ure.replace("*", "")) is not None


def test_verb_root_is_rejected_for_non_verb_derivative():
    """動詞定義寫成「to do X」，套到名詞上語法就不成句。

    實例：masturbate 的 "to touch or rub..." 掛在 masturbation 上。
    """
    data = _uro_entry("en*joy", "verb", ["to take pleasure in (something)"],
                      "en*joy*able", "adjective")
    assert mine._find_run_on(data, "enjoyable") is None


def test_agent_noun_derivative_is_rejected():
    """指人的衍生詞會拿到「事物或行為」的定義，語意直接錯掉。

    psychotherapist 拿到的是 psychotherapy 的「用談話治療心理疾病」——那是療法。
    單看詞性擋不掉，兩邊都是名詞。
    """
    data = _uro_entry("psy*cho*ther*a*py", "noun",
                      ["treatment of mental illness by talking about problems"],
                      "psy*cho*ther*a*pist", "noun")
    assert mine._find_run_on(data, "psychotherapist") is None


def test_agent_rule_catches_roots_that_also_end_in_er():
    """母詞自己也以 -er 結尾時規則仍要生效（gather → gatherer）。"""
    data = _uro_entry("gath*er", "verb", ["to bring things together"],
                      "gath*er*er", "noun")
    assert mine._find_run_on(data, "gatherer") is None


def test_ambiguous_run_on_is_rejected():
    """同一個衍生詞掛在兩個不同母詞底下時放棄——分不出該用哪一個。"""
    data = (_uro_entry("aaa", "adjective", ["first meaning"], "shared*ly", "adverb")
            + _uro_entry("bbb", "adjective", ["second meaning"], "shared*ly", "adverb"))
    assert mine._find_run_on(data, "sharedly") is None


def test_lookup_failure_is_distinguished_from_missing_word(monkeypatch):
    """查詢失敗與「字典沒收」都會讓 Definition 空白，但要分得出來——
    前者重跑就會有，後者重跑幾次都不會有。"""
    monkeypatch.setattr(mine, "MW_LEARNERS_KEY", "dummy")

    def boom(ref, key, w):
        raise RuntimeError("HTTP Error 429: Too Many Requests")

    monkeypatch.setattr(mine, "_mw_get", boom)
    assert mine.fetch_definition("whatever", sentence="A sentence.") == ""
    assert mine.LAST_FLAGS == {"lookupfail"}
    assert mine.UNCERTAIN_TAG["lookupfail"] == "definition-lookup-failed"


def test_nodef_reaches_the_note_tags(monkeypatch, tmp_path):
    seen = {}

    def fake_invoke(action, **kw):
        if action == "canAddNotes":
            return [True]
        if action == "addNote":
            seen["tags"] = kw["note"]["tags"]
            return 1

    monkeypatch.setattr(mine, "invoke", fake_invoke)
    monkeypatch.setattr(mine, "extract_audio", lambda *a, **k: None)
    monkeypatch.setattr(mine, "normalize_audio", lambda p: None)
    monkeypatch.setattr(mine, "store", lambda p, f: None)
    monkeypatch.setattr(mine, "fetch_synonyms", lambda *a, **k: "")
    monkeypatch.setattr(mine, "fetch_chinese", lambda *a, **k: "")
    monkeypatch.setattr(mine, "MEDIA_DIR", str(tmp_path))
    monkeypatch.setattr(mine, "fetch_definition",
                        lambda *a, **k: (mine.LAST_FLAGS.clear(),
                                         mine.LAST_FLAGS.add("nodef"), "")[2])
    sent = {"text": "Alpha bravo.", "start": 1.0, "end": 3.0, "nwords": 2, "from_json3": True}
    mine.add_card("vid", "v.mp4", sent, "aneurysm", "t")
    assert "no-definition" in seen["tags"]
