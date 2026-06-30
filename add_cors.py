"""把瀏覽器擴充的 origin 加進 AnkiConnect 的 webCorsOriginList。

安裝 asbplayer / Yomitan 後，到 chrome://extensions 開「開發人員模式」看每個擴充的 ID，
然後執行（可一次多個）：
    python add_cors.py chrome-extension://<asbplayer-id> chrome-extension://<yomitan-id>
改完需「完全關閉並重開 Anki」才生效。
"""
import json
import os
import re
import sys

META = os.path.expanduser(
    "~/Library/Application Support/Anki2/addons21/2055492159/meta.json"
)

# 只接受瀏覽器擴充 origin 或本機/asbplayer，避免誤開寬鬆來源擴大 AnkiConnect 暴露面
ALLOWED_RE = re.compile(
    r"^(chrome-extension://[a-p]{32}|moz-extension://[0-9a-f-]+|"
    r"https?://localhost(:\d+)?|https://app\.asbplayer\.dev)$"
)


def validate(origins):
    bad = [o for o in origins if not ALLOWED_RE.match(o)]
    if bad:
        sys.exit(
            "拒絕加入下列來源（只接受 chrome-extension://<32碼id>、moz-extension://、"
            f"http(s)://localhost、https://app.asbplayer.dev）：\n  " + "\n  ".join(bad)
        )


def main(origins):
    validate(origins)
    if not os.path.exists(META):
        sys.exit(f"找不到 AnkiConnect 設定檔：{META}\n請先安裝 AnkiConnect 並至少開過一次 Anki。")
    try:
        meta = json.load(open(META, encoding="utf-8"))
    except json.JSONDecodeError as ex:
        sys.exit(f"meta.json 不是合法 JSON：{ex}")
    if not isinstance(meta, dict):
        sys.exit("meta.json 結構異常（最外層不是物件）")

    cfg = meta.setdefault("config", {})
    if not isinstance(cfg, dict):
        sys.exit("meta.json 的 config 不是物件，請手動檢查設定檔")
    lst = cfg.setdefault("webCorsOriginList", [])
    if not isinstance(lst, list):
        sys.exit("meta.json 的 webCorsOriginList 不是陣列，請手動檢查設定檔")
    for o in origins:
        if o not in lst:
            lst.append(o)
            print(f"+ 加入 {o}")
        else:
            print(f"= 已存在 {o}")

    # atomic write：先寫 temp 再 replace，避免中途失敗弄壞設定檔
    tmp = META + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, META)
    print("目前 webCorsOriginList:", lst)
    print("⚠️ 請完全關閉並重開 Anki 後生效")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("用法: python add_cors.py chrome-extension://<id> [更多...]")
    main(sys.argv[1:])
