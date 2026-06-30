"""備援：用 genanki 產出含 note type 的空牌組 .apkg（spec §9 交付物）。
即使沒有 AnkiConnect，也能用 Anki 匯入此檔得到相同 note type。"""
import genanki
from anki import MODEL_NAME, DECK_NAME, FIELDS, FRONT, BACK, CSS

# 固定 ID：重匯入時不會產生重複 note type
MODEL_ID = 1607392319
DECK_ID = 2059400110

model = genanki.Model(
    MODEL_ID, MODEL_NAME,
    fields=[{"name": n} for n in FIELDS],
    templates=[{"name": "Card 1", "qfmt": FRONT, "afmt": BACK}],
    css=CSS,
)
deck = genanki.Deck(DECK_ID, DECK_NAME)

# genanki 只會打包「被 note 使用到的 model」，空 deck 不含 note type。
# 因此放一張範例卡（匯入後可刪），確保 .apkg 真的帶有 YT Mining EN note type。
deck.add_note(genanki.Note(
    model=model,
    fields=[
        "example",                                  # Word
        "This is an <b>example</b> card.",          # Sentence
        "(可刪除此範例卡，note type 會保留)",          # Definition
        "範例", "", "",                              # Chinese, Collocation, Synonyms
        "", "", "",                                 # SentenceAudio, WordAudio, Image
        "YT Mining EN template", "",                # Source, URL
    ],
    tags=["youtube-mining", "example"],
))
genanki.Package(deck).write_to_file("YT_Mining_EN.apkg")
print("✓ 已輸出 YT_Mining_EN.apkg（含 1 張範例卡，匯入後可刪）")
