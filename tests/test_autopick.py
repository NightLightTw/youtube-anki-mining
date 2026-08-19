"""autopick 挑字邏輯的回歸測試。

案例全部來自實際影片處理時踩過、修過的坑（詳見 git log 與 README「已知限制」），
目的是讓未來修改不會讓這些已修復的行為悄悄退化。
"""
from autopick import (
    _is_spelled_number,
    auto_select,
    lemma,
    proper_nouns,
    sentence_unknowns,
)


def unknowns(text, known=(), proper=(), min_zipf=0.0, max_zipf=8.0):
    """預設把頻率帶開到最寬，讓各測試聚焦在自己要驗證的那一個過濾器上。"""
    return sentence_unknowns(text, set(known), set(proper), min_zipf, max_zipf)


def surfaces(result):
    return [surface for surface, _lemma, _zipf in result]


# ---------- 拼寫數字過濾（fifty-nine 該擋、fifty-fifty 不該擋）----------

def test_spelled_numbers_are_filtered():
    for w in ["nineteen", "fourteen", "fifty-nine", "forty-five", "twenty-one",
              "hundred", "sixty-seven"]:
        assert _is_spelled_number(w), w


def test_reduplicated_number_idioms_are_kept():
    # 兩段完全相同（fifty-fifty）是「對半」慣用語，不是拼出一個數字
    assert not _is_spelled_number("fifty-fifty")
    assert not _is_spelled_number("twenty-twenty")


def test_normal_words_are_not_spelled_numbers():
    for w in ["hello", "hunter-gatherer", "one-sided", "self-esteem"]:
        assert not _is_spelled_number(w), w


def test_spelled_number_excluded_from_sentence_but_idiom_kept():
    assert "fifty-nine" not in surfaces(unknowns("She counted fifty-nine sheep"))
    assert "fifty-fifty" in surfaces(unknowns("The odds are fifty-fifty at this point"))


# ---------- 口語斷詞殘片（"gonna ch- turn a corner"）----------

def test_trailing_hyphen_disfluency_is_filtered():
    result = unknowns("We are gonna ch- turn a corner")
    assert all(not s.endswith("-") for s in surfaces(result))
    assert "ch" not in [lm for _s, lm, _z in result]


def test_legit_hyphenated_word_survives_disfluency_filter():
    # 合法連字詞的連字號夾在字母中間、不在字尾，不該被誤殺
    assert "one-sided" in surfaces(unknowns("It was a very one-sided debate"))


# ---------- 口語拼寫/填充詞黑名單 ----------

def test_non_learning_words_are_filtered():
    result = surfaces(unknowns("Yeah we are gonna wanna do it kinda soon"))
    for w in ["Yeah", "gonna", "wanna", "kinda"]:
        assert w not in result


# ---------- 縮寫與專有名詞 ----------

def test_apostrophe_contractions_are_skipped():
    for s in surfaces(unknowns("I didn't know that they'll leave")):
        assert "'" not in s and "’" not in s


def test_mid_sentence_capitalized_word_is_skipped():
    assert "Anna" not in surfaces(unknowns("Yesterday Anna told me a story"))


def test_proper_noun_preseen_mid_sentence_also_excluded_at_sentence_start():
    proper = proper_nouns([{"text": "Yesterday Anna told me a story"}])
    assert lemma("Anna") in proper
    # 之後 Anna 就算出現在句首（大寫、idx==0）也一併排除
    assert "Anna" not in surfaces(unknowns("Anna told me a story", proper=proper))


# ---------- 詞形還原覆寫（simplemma 修壞的字）----------

def test_irregular_verb_lemma_overrides():
    assert lemma("went") == "go"
    assert lemma("gone") == "go"
    assert lemma("ran") == "run"


def test_identity_lemma_overrides_for_ing_ed_forms():
    # 這些 -ing/-ed 形可能是合法的獨立名詞/形容詞，覆寫成自己而非動詞原形
    for w in ["thinking", "growing", "talking", "played", "fixed", "developed"]:
        assert lemma(w) == w, w


def test_british_double_l_lemma_overrides():
    assert lemma("travelling") == "travel"
    assert lemma("modelling") == "model"
    assert lemma("counselling") == "counseling"


def test_plural_override():
    assert lemma("taxes") == "tax"


# ---------- i+1 選句 ----------

def _sent(text):
    return {"text": text, "start": 0.0, "end": 3.0, "nwords": len(text.split())}


def _known_lemmas(*words):
    """known 集合比對的是 lemma；用 lemma() 本身來建集合，
    測試就不會對 simplemma 特定版本的還原結果（sat→sit）硬編碼依賴。"""
    return {lemma(w) for w in words}


def test_auto_select_keeps_exactly_one_unknown_per_sentence():
    known = _known_lemmas("the", "cat", "sat", "on", "a", "dog", "chased")
    sentences = [
        _sent("The cat sat on the mat"),      # 一個生字 mat → 入選
        _sent("A dog chased a fox and a badger"),  # 兩個生字 fox/badger → 淘汰
    ]
    picks = auto_select(sentences, known, min_words=3, max_words=30,
                        min_zipf=0.0, max_zipf=8.0)
    assert [c["lemma"] for c in picks] == [lemma("mat")]


def test_auto_select_keeps_shortest_sentence_per_lemma():
    known = _known_lemmas("the", "cat", "sat", "on", "a", "big", "and",
                          "very", "old")
    sentences = [
        _sent("The very big and very old cat sat on the mat"),
        _sent("The cat sat on the mat"),
    ]
    picks = auto_select(sentences, known, min_words=3, max_words=30,
                        min_zipf=0.0, max_zipf=8.0)
    assert len(picks) == 1
    assert picks[0]["sent"]["nwords"] == 6
