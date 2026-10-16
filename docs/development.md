# 開發指南

給想改程式或送 PR 的人。只想用工具的話看 [README](../README.md) 就夠了。

## 環境

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt pytest
```

`pytest` 不在 `requirements.txt` 裡，因為一般使用者用不到；CI 也是另外裝的。

開發環境是 macOS（Apple Silicon）。`run.sh` 用到 `open -a Anki` 這類 macOS 指令，但測試不需要 Anki、MW 金鑰或網路，在 Linux 上也能跑（CI 就是 Ubuntu）。

## 跑測試

```bash
.venv/bin/python -m pytest -q
```

測試的說明大多寫著當初是哪張卡、哪支影片出的錯。改到某個規則時，先看對應的測試在保護什麼，再決定能不能動。

新增規則時，請確認測試**真的抓得到它**：暫時把那個判斷拿掉或改成 `if False:`，測試要失敗。只檢查「沒有挑到某個字」的測試，在整個字被誤殺時也會通過，所以常要再配一條正向的測試（例如 `test_crew_is_excluded_by_frequency_not_by_being_dropped`）。

## 改了 README 之後

`README.zh-CN.md` 是從 `README.md` 自動轉出來的，**不要直接改它**。改完繁體版後重新產生：

```bash
.venv/bin/pip install opencc==1.4.1
.venv/bin/python scripts/gen_readme_zh_cn.py
```

CI 會用 `--check` 逐字比對兩份檔案，OpenCC 版本不同轉出來的字就不同，所以版本要跟 CI 一致。OpenCC 偶爾會轉錯（例如「核心」轉成「内核」），遇到時加進 `scripts/gen_readme_zh_cn.py` 的 `POST_REPLACEMENTS`。

## 送出前

- `.env` 放的是 MW 金鑰，已經在 `.gitignore` 裡，提交前再確認一次沒有被加進去。
- 全部測試要通過。CI 只在 push 到 `main` 和開 PR 時跑，push 到其他分支不會觸發，所以送 PR 前先在本機跑一次。
- commit 訊息目前用繁體中文，格式是 `fix:`、`docs:`、`chore:` 這類前綴加一句說明。

## 先讀這兩份

- [技術雷區](pitfalls.md)：AnkiConnect、Merriam-Webster、simplemma、音檔切點、yt-dlp 已知會出錯的地方。很多看起來多餘的程式碼，都是為了繞過這些問題。
- [逐卡稽核](card-audit.md)：管線做完後怎麼檢查卡片，以及最常見的錯誤類型。想改善挑字或釋義品質，從這裡找案例。

詳細的量測與討論在 [issues](https://github.com/NightLightTw/youtube-anki-mining/issues)：#1 音檔切點、#2 衍生詞查不到定義、#3 詞義消歧、#4 用 LLM 修卡、#5 靜音吸附、#6 配圖。
