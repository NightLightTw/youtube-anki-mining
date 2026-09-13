"""從 README.md（繁體）自動產生 README.zh-CN.md（簡體）。

用 OpenCC 的 tw2sp 設定（台灣正體 → 大陸簡體，含兩岸慣用語轉換，
例如「影片→视频」「網址→网址」），不是逐字簡化而已。

只有維護者與 CI 需要跑這個腳本，一般使用者不用：
    pip install opencc==1.4.1
（版本需與 .github/workflows/ci.yml 釘的一致：--check 做完整字串比對，
 兩邊 OpenCC 詞庫版本不同會誤報不同步）

用法：
    python scripts/gen_readme_zh_cn.py          # 重新生成 README.zh-CN.md
    python scripts/gen_readme_zh_cn.py --check  # 只檢查是否同步（CI 用），落後時 exit 1
"""
import pathlib
import sys

import opencc

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "README.md"
DST = ROOT / "README.zh-CN.md"

HEADER = ("<!-- 本文件由 scripts/gen_readme_zh_cn.py 从 README.md 自动转换生成，"
          "请勿直接编辑；内容以繁体版 README.md 为准 -->\n\n")

# 語言切換行不能整行交給 OpenCC 轉（連結會指向自己），先換成占位符、轉完再換回反向連結
SWITCH_TW = "**繁體中文** | [简体中文](README.zh-CN.md)"
SWITCH_CN = "[繁體中文](README.md) | **简体中文**"
PLACEHOLDER = "@@LANG_SWITCH@@"

# tw2sp 涵蓋不到的兩岸慣用詞差異，以及 tw2sp 自己轉壞的詞，轉換後再做目標式替換。
# 這裡是無條件的整份 str.replace，新增前先 grep 全文確認該詞在各處替換都安全；
# 詞義會隨 README 內容改變，例如哪天真的寫到 Linux kernel，「内核」那條就得收窄。
POST_REPLACEMENTS = {
    "单字": "单词",   # 台灣「單字」＝大陸「单词」
    "透过": "通过",   # 台灣「透過」＝大陸「通过」
    # 以下是 tw2sp 誤轉，不是用詞差異
    "内核": "核心",   # 「核心特色」「核心 Python 管線」被當成 kernel 轉掉；
                      # 「同步的核心」那處 tw2sp 沒轉，本來就是「核心」，不受影響
    "句辅音档": "句子音档",  # 「句子＋音檔」被斷成「句＋子音＋檔」，子音→辅音
}


def generate():
    text = SRC.read_text(encoding="utf-8")
    if SWITCH_TW not in text:
        sys.exit(f"✗ README.md 裡找不到語言切換行：{SWITCH_TW!r}")
    text = text.replace(SWITCH_TW, PLACEHOLDER)
    converted = opencc.OpenCC("tw2sp").convert(text)
    for old, new in POST_REPLACEMENTS.items():
        converted = converted.replace(old, new)
    return HEADER + converted.replace(PLACEHOLDER, SWITCH_CN)


def main():
    expected = generate()
    if "--check" in sys.argv[1:]:
        actual = DST.read_text(encoding="utf-8") if DST.exists() else ""
        if actual != expected:
            sys.exit("✗ README.zh-CN.md 與 README.md 不同步。"
                     "請執行 python scripts/gen_readme_zh_cn.py 重新生成後一併提交。")
        print("✓ README.zh-CN.md 與 README.md 同步")
        return
    DST.write_text(expected, encoding="utf-8")
    print(f"✓ 已生成 {DST.name}（{len(expected)} 字元）")


if __name__ == "__main__":
    main()
