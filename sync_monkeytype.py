"""把 Anki 卡片（YouTube Mining deck）同步成 monkeytype 的自訂語言與例句庫。

用法：
    .venv/bin/python sync_monkeytype.py            # 同步整個 deck
    .venv/bin/python sync_monkeytype.py --due      # 只同步今天到期（待複習）的卡片
    .venv/bin/python sync_monkeytype.py --limit 50 # 只取最新 50 張

產出（monkeytype 的 vite dev server 會直接吃，重新整理頁面即生效）：
    frontend/static/languages/english_anki.json  ← words mode 用的生字庫
    frontend/static/quotes/english_anki.json     ← quote mode 用的例句庫

需求：Anki 桌面版開著（AnkiConnect 跟 mining pipeline 用同一個）。
"""
import argparse
import html
import json
import re
from pathlib import Path

from anki import invoke, DECK_NAME

MONKEYTYPE_DIR = Path(__file__).resolve().parent.parent / "monkeytype"
LANG_FILE = MONKEYTYPE_DIR / "frontend/static/languages/english_anki.json"
QUOTES_FILE = MONKEYTYPE_DIR / "frontend/static/quotes/english_anki.json"

TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")


def strip_html(text: str) -> str:
    """去掉 Anki 欄位裡的標色 span 等 HTML，還原純文字句子。"""
    text = TAG_RE.sub("", text)
    text = html.unescape(text)
    return SPACE_RE.sub(" ", text).strip()


def fetch_notes(due_only: bool, limit: int | None) -> list[dict]:
    query = f'deck:"{DECK_NAME}"'
    if due_only:
        query += " is:due"
    note_ids = invoke("findNotes", query=query)
    if not note_ids:
        return []
    note_ids = sorted(note_ids, reverse=True)  # noteId 即建立時間戳，新卡在前
    if limit:
        note_ids = note_ids[:limit]
    return invoke("notesInfo", notes=note_ids)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--due", action="store_true", help="只同步今天到期的卡片")
    parser.add_argument("--limit", type=int, help="最多取幾張（取最新的）")
    args = parser.parse_args()

    notes = fetch_notes(args.due, args.limit)
    if not notes:
        raise SystemExit("找不到卡片（deck 是空的，或 --due 模式下今天沒有到期卡）")

    words: list[str] = []
    seen_words: set[str] = set()
    quotes: list[dict] = []
    seen_sentences: set[str] = set()

    for note in notes:
        fields = note["fields"]
        word = strip_html(fields["Word"]["value"]).lower()
        sentence = strip_html(fields["Sentence"]["value"])
        source = strip_html(fields["Source"]["value"]) or "YouTube Mining"

        if word and word not in seen_words:
            seen_words.add(word)
            words.append(word)

        if sentence and sentence not in seen_sentences:
            seen_sentences.add(sentence)
            quotes.append(
                {
                    "text": sentence,
                    "source": source,
                    "length": len(sentence),
                    "id": len(quotes) + 1,
                }
            )

    LANG_FILE.write_text(
        json.dumps(
            {"name": "english_anki", "noLazyMode": True, "words": words},
            ensure_ascii=False,
            indent=2,
        )
    )
    QUOTES_FILE.write_text(
        json.dumps(
            {
                "language": "english_anki",
                "groups": [[0, 100], [101, 300], [301, 600], [601, 9999]],
                "quotes": quotes,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print(f"✔ {len(words)} 個生字 → {LANG_FILE}")
    print(f"✔ {len(quotes)} 句例句 → {QUOTES_FILE}")


if __name__ == "__main__":
    main()
