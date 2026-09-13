"""autopick 挑字邏輯的回歸測試。

案例全部來自實際影片處理時踩過、修過的坑（詳見 git log 與 README「已知限制」），
目的是讓未來修改不會讓這些已修復的行為悄悄退化。
"""
import pytest

import autopick

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


def test_bussed_lemma_override():
    # simplemma 誤還原成罕見古字 "buss"（親吻），跟公車完全無關
    assert lemma("bussed") == "bus"


def test_preferred_identity_override():
    # simplemma 誤還原成不存在的字 "preferr"
    assert lemma("preferred") == "preferred"
    assert lemma("preferring") == "preferring"


def test_british_double_l_ed_form_overrides():
    # 跟既有的 -ing 形覆寫（travelling→travel 等）是同一批字的 -ed 形，同樣的錯誤
    assert lemma("travelled") == "travel"
    assert lemma("modelled") == "model"
    assert lemma("cancelled") == "cancel"
    assert lemma("labelled") == "label"
    assert lemma("signalled") == "signal"


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


@pytest.mark.parametrize("surface,expected", [
    ("shook", "shake"),        # 不規則過去式，simplemma 原樣回傳
    ("drowning", "drown"),     # 動名詞，同批的 drowned 卻正確
])
def test_irregular_forms_are_normalised(surface, expected):
    """沒還原的變化形會直接變成卡片上的目標字，而字典查不到那個形態、定義留空。

    實測 shook 與 drowning 都是這樣進到牌組的；同一批的 drowned／wept／stung
    simplemma 都還原正確，所以只能逐字補進覆寫表。
    """
    assert lemma(surface) == expected


def test_third_person_verb_is_not_stripped_to_a_rare_real_word():
    """-es 只被剝掉一個 s 時，殘字有時剛好是個罕見真字，會躲過詞頻篩選。

    這比還原成「不存在的字」更難發現：crosse 查得到（是長曲棍球棒），只是跟句子
    裡的動詞 cross 完全無關。
    """
    assert lemma("crosses") == "cross"


def test_the_crosse_card_would_not_be_created_now():
    """真正的症狀在下游：錯誤的還原結果會躲過詞頻上限，變成一張卡。

    zipf('crosse')=2.89 落在挑字區間內，所以當初建出了 Word=crosse 的卡（定義查無、
    中文欄變成音譯）。還原正確後 zipf('cross')=5.0 超過 MAX_ZIPF，這個字會被濾掉。
    """
    picked = unknowns("but what we saw obviously crosses a line.",
                      min_zipf=autopick.MIN_ZIPF, max_zipf=autopick.MAX_ZIPF)
    assert "crosse" not in [lm for _, lm, _ in picked]
    assert "cross" not in [lm for _, lm, _ in picked]


def test_british_past_tense_is_not_collapsed_to_its_base_verb():
    """crew 是 crow(公雞啼叫) 的過去式（MW 標 chiefly British），會被還原成 crow。

    現代英文裡 crew 幾乎只當「船員」解，縮並過去會讓卡片的目標字根本沒出現在句子裡
    ——實測 "that's when the crew learns the real plan" 建出了 Word=crow 的卡。
    """
    assert lemma("crew") == "crew"


def test_the_crow_card_would_not_be_created_now():
    """真正的症狀在下游：crew 的詞頻本來就超過上限，錯誤的還原讓它鑽過篩選。"""
    picked = unknowns("and that's when the crew learns the real plan.",
                      min_zipf=autopick.MIN_ZIPF, max_zipf=autopick.MAX_ZIPF)
    assert "crow" not in [lm for _, lm, _ in picked]
    assert "crew" not in [lm for _, lm, _ in picked]


def test_crew_is_excluded_by_frequency_not_by_being_dropped():
    """把頻率帶開到最寬時，crew 要以自己的身分出現。

    上面那個測試只驗「挑不到」，但「整個 token 被丟掉」也會讓它通過。這一條確認
    crew 仍被當成一個正常的字看待，只是詞頻超過上限才落選——日後若有人改動 token
    過濾邏輯而誤殺 crew，上面那條抓不到，這條會。
    """
    got = unknowns("and that's when the crew learns the real plan.")
    assert ("crew", "crew") in [(s, lm) for s, lm, _ in got]
