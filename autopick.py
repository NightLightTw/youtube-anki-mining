"""全自動挑字（spec §9 進階）：

1. 從現有 Anki 牌組撈「已學單字」建 known set
2. 對重建後的句子，找出「不在 known set、且頻率落在合理帶」的生字
3. 只留「整句剛好一個生字」(i+1) 的句子，作為自動製卡候選
"""
import re

import simplemma
from wordfreq import zipf_frequency

from anki import invoke

# 值得學的頻率帶（Zipf）：低於 = 太罕見；高於 = 太基礎/功能詞
# 同時作為 auto_select() 的預設值，與 mine.py CLI 的 --min/max-zipf 預設一致
MIN_ZIPF = 2.5
MAX_ZIPF = 4.2

# 常見英文名（小寫）：只出現在句首的人名，靠句中大寫預掃抓不到，用這份兜底
COMMON_NAMES = {
    "anna", "jake", "mike", "john", "mary", "james", "robert", "michael",
    "david", "william", "richard", "joseph", "thomas", "charles", "daniel",
    "matthew", "anthony", "mark", "paul", "steven", "andrew", "kenneth",
    "george", "joshua", "kevin", "brian", "edward", "ronald", "timothy",
    "jason", "jeffrey", "gary", "ryan", "nicholas", "eric", "jacob", "jordan",
    "patricia", "jennifer", "linda", "elizabeth", "barbara", "susan",
    "jessica", "sarah", "karen", "nancy", "lisa", "betty", "sandra",
    "emily", "emma", "olivia", "sophia", "isabella", "ava", "amy", "laura",
    "peter", "henry", "jack", "sam", "tom", "ben", "tony", "chris", "alex",
    "google", "youtube", "anki", "english",
}

# 口語拼寫變體與填充詞：頻率恰好落在生字帶(2.5~4.2)、卻做不成有意義的卡——
# 它們是標準詞的非正式拼寫(cuz=because、lemme=let me)或純語氣詞(hmm、wow)，
# Merriam-Webster 多半查無詞條。高頻的(gonna/yeah zipf>4.2)本來就被頻率上限擋掉，
# 這裡專門補中頻漏網的那批。注意：不用「MW 查無定義就跳過」兜底，因為 calmly、
# adipose、ghee 這類正規但 MW Learner's 缺條目的字也會被誤殺，必須是明確的黑名單。
NON_LEARNING_WORDS = {
    # 口語縮寫拼寫。刻意不含也有正當詞義的字：til(芝麻)、cos(cosine)、em(印刷單位
    # em/em dash)——它們頻率落在生字帶，放進黑名單會誤殺正常用法。
    "cuz", "coz", "sorta", "kinda", "dunno", "lemme", "gimme",
    "gonna", "wanna", "gotta", "gotcha", "coulda", "woulda", "shoulda", "outta",
    "ya", "yer", "tryna", "innit", "aint",
    # 語氣詞 / 填充詞 / 狀聲
    "yeah", "yep", "yup", "nope", "nah", "uh", "um", "umm", "uhh", "hmm", "hmmm",
    "mmm", "ooh", "ohh", "aha", "aww", "ugh", "huh", "whoa", "woah", "oops",
    "yay", "hooray", "haha", "hehe", "heh", "lol", "duh", "meh", "psst", "shh",
    "wooo", "yikes", "phew", "eh", "hey", "hiya", "yo",
}

_lemma_cache = {}

# simplemma 對少數字有錯誤的還原結果（已知瑕疵），在此覆寫成正確原形。
# 例：lemmatize("slang") 會誤回傳 "sling"，導致整張卡查到完全不同的字。
LEMMA_OVERRIDES = {
    "slang": "slang",     # 誤還原成 sling
    "taxes": "tax",       # 誤還原成 taxis（計程車複數），導致整張卡查錯字
    "putting": "put",     # 誤還原成 putt（高爾夫推桿），導致 put down(存入) 誤判成高爾夫術語
    # 英式雙l動名詞系統性瑕疵：simplemma 對「-el/-am 結尾+雙l+ing」的英式拼法常還原
    # 錯誤，誤還原成「不存在的殘字」（travell/modell/cancell/labell/signall），比查錯字
    # 更糟——這些殘字會直接變成卡片上的目標字，讓學習者背一個不存在的英文字。
    # 這裡的覆寫值必須是「最終正確的原形」，不能是中繼字串：LEMMA_OVERRIDES 命中時
    # lemma() 會直接回傳覆寫值，不會再送進 simplemma 處理一次，所以要填 simplemma
    # 對「美式單l拼法」還原後的最終結果（travel/model/cancel/label/signal），而不是
    # 美式拼法本身（simplemma.lemmatize('traveling') 還會再還原成 'travel'）。
    # counselling 是例外：simplemma.lemmatize('counseling') 還原結果就是 'counseling'
    # 本身（不再往下縮），所以覆寫值維持美式拼法不變。
    "travelling": "travel",
    "modelling": "model",
    "cancelling": "cancel",
    "labelling": "label",
    "signalling": "signal",
    "counselling": "counseling",
}


def lemma(w):
    w = w.lower()
    if w in LEMMA_OVERRIDES:
        return LEMMA_OVERRIDES[w]
    if w not in _lemma_cache:
        _lemma_cache[w] = simplemma.lemmatize(w, lang="en")
    return _lemma_cache[w]


# 只用 [A-Za-z] 會把含重音字母的外來詞從中間切斷，例如 "appétit" 的 é 不在範圍內，
# 導致被切成 "app"+"tit" 兩個假單字（"tit" 恰好是個真實英文字，就會被誤選成生字候選）。
# 補上拉丁重音字母範圍（涵蓋 café/naïve/jalapeño 這類常見外來詞），整詞保留不被切斷。
WORD_CHARS = r"A-Za-zÀ-ÖØ-öø-ÿ"

# YouTube 字幕的縮寫引號常是彎引號(U+2019 ’)而非直引號(')，只認直引號會把
# "didn't" 這類字從彎引號處切斷成殘缺片段 "didn"（長度>=3、剛好不是已知字，
# 就會被誤選成候選字）。兩種引號都要能組成完整詞。
APOSTROPHES = "'’"


def _clean_first_field(val):
    val = re.sub(r"\[sound:[^\]]*\]", " ", val)
    val = re.sub(r"<[^>]+>", " ", val)
    val = re.sub(r"\([^)]*\)", " ", val)        # 去 (n.) (v.) 等詞性標記
    val = re.sub(rf"[^{WORD_CHARS}\s{APOSTROPHES}-]", " ", val)
    return val.lower().split()


def build_known_set(max_tokens=3):
    """已知字 = 各 note「第一欄」（≤max_tokens 詞）的 lemma 集合。

    用 AnkiConnect 回傳欄位的 order 屬性判斷第一欄，不依賴 dict 順序假設。
    """
    known = set()
    nids = invoke("findNotes", query="deck:*")
    for i in range(0, len(nids), 200):
        for info in invoke("notesInfo", notes=nids[i:i + 200]):
            fields = info.get("fields") or {}
            if not fields:
                continue
            first = min(fields, key=lambda k: fields[k].get("order", 0))
            toks = _clean_first_field(fields[first]["value"])
            if 1 <= len(toks) <= max_tokens:
                for t in toks:
                    if len(t) >= 2:
                        known.add(lemma(t))
    return known


def proper_nouns(sentences):
    """預掃：任何在句中(非句首)以大寫出現的字，視為專有名詞 (Anna, Jake...)，
    之後連它出現在句首時也一併排除。"""
    pn = set()
    for s in sentences:
        toks = re.findall(rf"[{WORD_CHARS}][{WORD_CHARS}{APOSTROPHES}-]+", s["text"])
        for idx, w in enumerate(toks):
            if idx > 0 and w[0].isupper():
                pn.add(lemma(w))
    return pn


def sentence_unknowns(text, known, proper, min_zipf, max_zipf):
    """回傳句中的生字 [(surface, lemma, zipf)]，已去已知字/功能詞/專有名詞。"""
    tokens = re.findall(rf"[{WORD_CHARS}][{WORD_CHARS}{APOSTROPHES}-]+", text)
    out, seen = [], set()
    for idx, w in enumerate(tokens):
        if idx > 0 and w[0].isupper():     # 句中大寫 → 專有名詞
            continue
        if any(c in w for c in APOSTROPHES):  # 縮寫 (here's, we'll) → 跳過
            continue
        lm = lemma(w)
        # 專有名詞：句中大寫預掃，或「大寫且在常見人名表」（限大寫，免誤排 mark/mike 等小寫常用字）
        if lm in proper or (w[0].isupper() and lm in COMMON_NAMES):
            continue
        if w.lower() in NON_LEARNING_WORDS or lm in NON_LEARNING_WORDS:
            continue    # 口語拼寫/填充詞：做不成有意義的卡
        if lm in known or lm in seen or len(lm) < 3:
            continue
        z = zipf_frequency(lm, "en")
        if z < min_zipf or z > max_zipf:
            continue
        seen.add(lm)
        out.append((w, lm, z))
    return out


def auto_select(sentences, known, min_words=6, max_words=20,
                min_zipf=MIN_ZIPF, max_zipf=MAX_ZIPF):
    """挑出 i+1 句子（整句剛好一個生字），每個生字只留一句（取最短者）。"""
    proper = proper_nouns(sentences)
    best = {}  # lemma -> 候選
    for s in sentences:
        if not (min_words <= s["nwords"] <= max_words):
            continue
        unk = sentence_unknowns(s["text"], known, proper, min_zipf, max_zipf)
        if len(unk) != 1:          # i+1：剛好一個生字
            continue
        surface, lm, z = unk[0]
        cand = {"sent": s, "surface": surface, "lemma": lm, "zipf": z}
        if lm not in best or s["nwords"] < best[lm]["sent"]["nwords"]:
            best[lm] = cand
    # 進階字優先：頻率帶內由難到易（已濾掉太罕見的，剩下的越進階 CP 值越高）
    return sorted(best.values(), key=lambda c: c["zipf"])
