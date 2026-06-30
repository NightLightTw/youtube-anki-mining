"""依 spec §3 建立 deck「YouTube Mining」與 note type「YT Mining EN」。可重複執行（idempotent）。"""
from anki import invoke, DECK_NAME, MODEL_NAME, FIELDS, FRONT, BACK, CSS


def main():
    # 1) Deck
    decks = invoke("deckNames")
    if DECK_NAME in decks:
        print(f"= deck 已存在：{DECK_NAME}")
    else:
        invoke("createDeck", deck=DECK_NAME)
        print(f"+ 建立 deck：{DECK_NAME}")

    # 2) Note type
    models = invoke("modelNames")
    if MODEL_NAME in models:
        print(f"= note type 已存在：{MODEL_NAME}（補欄位、校正順序、更新模板與 CSS）")
        # 補上新版才有的欄位（如 Synonyms / Chinese），保留既有卡片
        existing = invoke("modelFieldNames", modelName=MODEL_NAME)
        for i, f in enumerate(FIELDS):
            if f not in existing:
                invoke("modelFieldAdd", modelName=MODEL_NAME, fieldName=f, index=i)
                print(f"  + 新增欄位：{f} (位置 {i})")
        # 把欄位重排成 FIELDS 的順序（曾改過順序也能修正；多餘欄位保留在後面）
        for i, f in enumerate(FIELDS):
            invoke("modelFieldReposition", modelName=MODEL_NAME, fieldName=f, index=i)
        extra = [f for f in invoke("modelFieldNames", modelName=MODEL_NAME)
                 if f not in FIELDS]
        if extra:
            print(f"  ⚠️ note type 有多餘欄位（保留未刪）：{extra}")
        invoke("updateModelTemplates", model={
            "name": MODEL_NAME,
            "templates": {"Card 1": {"Front": FRONT, "Back": BACK}},
        })
        invoke("updateModelStyling", model={"name": MODEL_NAME, "css": CSS})
    else:
        invoke(
            "createModel",
            modelName=MODEL_NAME,
            inOrderFields=FIELDS,
            css=CSS,
            isCloze=False,
            cardTemplates=[{"Name": "Card 1", "Front": FRONT, "Back": BACK}],
        )
        print(f"+ 建立 note type：{MODEL_NAME}")

    # 3) 驗證（前 len(FIELDS) 欄須與 FIELDS 完全相符；多餘欄位允許保留在後）
    got_fields = invoke("modelFieldNames", modelName=MODEL_NAME)
    print(f"  欄位順序：{got_fields}")
    if got_fields[:len(FIELDS)] != FIELDS:
        raise RuntimeError(f"欄位順序不符！期望前綴 {FIELDS}，實際 {got_fields}")
    print("✓ 欄位順序正確")


if __name__ == "__main__":
    main()
