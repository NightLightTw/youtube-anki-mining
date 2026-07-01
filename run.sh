#!/usr/bin/env bash
# 一鍵跑完整流程：貼一個 YouTube 網址 -> 自動挑字做卡 -> 同步到 AnkiWeb。
#
# 用法：
#   ./run.sh "https://youtu.be/xxxxxxxxxxx"
#   ./run.sh "https://youtu.be/xxxxxxxxxxx" --max-cards 15   # 額外參數會原封傳給 mine.py --auto
#
# 前置需求：已跑過 pip install -r requirements.txt、setup_anki.py，且 .env 內有 MW 金鑰（可選）。
set -euo pipefail

# 出錯時明確指出是哪一行、剛才印出的最後一個「== 步驟 ==」是什麼，方便定位。
# trap 要在任何可能失敗的指令（含 cd）之前裝好，否則那個指令失敗時不會走這個訊息。
LAST_STEP="(尚未開始)"
on_error() {
  echo "" >&2
  echo "✗ 執行失敗：在「$LAST_STEP」這一步中斷（run.sh 第 $1 行）" >&2
  echo "  已完成的下載/卡片不會遺失；修正問題後可直接重新執行同一個指令。" >&2
}
trap 'on_error $LINENO' ERR
trap 'echo ""; echo "✗ 使用者中斷 (Ctrl-C)。已完成的下載/卡片不會遺失，可重新執行。" >&2; exit 130' INT

step() { LAST_STEP="$1"; echo "== $1 =="; }

step "切換到專案目錄"
cd "$(dirname "${BASH_SOURCE[0]}")"

if [ $# -lt 1 ]; then
  echo "用法: $0 <YouTube網址> [額外傳給 mine.py --auto 的參數...]" >&2
  exit 1
fi

URL="$1"; shift
EXTRA_ARGS=("$@")
PY=".venv/bin/python"
YTDLP=".venv/bin/yt-dlp"

# 0) 前置環境檢查：venv / yt-dlp / ffmpeg 沒裝好時，給清楚的指引而非隱晦的錯誤
step "檢查環境"
if [ ! -x "$PY" ] || [ ! -x "$YTDLP" ]; then
  echo "✗ 找不到 .venv 或其中的 python/yt-dlp。請先執行：" >&2
  echo "    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "✗ 找不到 ffmpeg。請先執行：brew install ffmpeg" >&2
  exit 1
fi

# 1) 從網址抽出 video id（支援 youtu.be/xxx、watch?v=xxx、shorts/xxx，含 ?si= 等參數）
# --no-playlist：網址若帶 list= 或本身是播放清單，只處理該支影片，避免誤下載整個清單
step "解析影片網址"
VIDEO_ID=$("$YTDLP" --no-warnings --no-playlist --print "%(id)s" "$URL")
if [ -z "$VIDEO_ID" ] || ! [[ "$VIDEO_ID" =~ ^[A-Za-z0-9_-]{11}$ ]]; then
  echo "✗ 無法解析出單一有效的 video id，請確認網址是否正確：$URL" >&2
  exit 1
fi
echo "  影片 ID: $VIDEO_ID"

# 2) 確認 AnkiConnect 連得上；沒開就啟動 Anki 並等待就緒（實際最多 40 秒，用 SECONDS 算 deadline）
step "確認 Anki / AnkiConnect"
ANKI_ADDR="127.0.0.1:8765"   # 與 anki.py 的 ANKI_URL 保持一致
anki_ready() {
  curl -s --connect-timeout 1 --max-time 1 "$ANKI_ADDR" \
    -X POST -d '{"action":"version","version":6}' 2>/dev/null | grep -q '"result"'
}
if ! anki_ready; then
  echo "  Anki 未開啟，正在啟動..."
  open -a Anki
  READY=0
  DEADLINE=$((SECONDS + 40))
  while [ "$SECONDS" -lt "$DEADLINE" ]; do
    if anki_ready; then READY=1; break; fi
    sleep 2
  done
  if [ "$READY" -ne 1 ]; then
    echo "✗ 等待 40 秒後仍連不上 AnkiConnect ($ANKI_ADDR)。" >&2
    echo "  請確認 Anki 已完全啟動、AnkiConnect add-on 已安裝，再重新執行。" >&2
    exit 1
  fi
  echo "✓ AnkiConnect 已就緒"
fi

# 3) 抓標題
step "取得影片標題"
TITLE=$("$YTDLP" --no-warnings --no-playlist --skip-download --print "%(title)s" "$URL")
echo "  標題: $TITLE"

# 4) 下載英文自動字幕（若已存在則 yt-dlp 會直接覆蓋/略過，天然可重跑）
step "下載字幕"
"$YTDLP" --no-warnings --no-playlist --skip-download --write-auto-subs --sub-lang en \
  --sub-format srt --convert-subs srt -o "media/%(id)s.%(ext)s" "$URL"
SRT="media/${VIDEO_ID}.en.srt"
if [ ! -s "$SRT" ]; then
  echo "✗ 找不到英文字幕檔：$SRT" >&2
  echo "  這支影片可能沒有英文（自動）字幕，無法用本工具製卡。" >&2
  exit 1
fi

# 5) 下載 360p 影片（做音檔/截圖用；若已存在會被覆蓋，可重跑）
step "下載影片 (360p)"
"$YTDLP" --no-warnings --no-playlist -f "best[height<=360]/bestvideo[height<=360]+bestaudio/best" \
  --merge-output-format mp4 -o "media/%(id)s.%(ext)s" "$URL"

# 6) 全自動挑字製卡（已挖過的字會被去重跳過，可重跑不會重複灌卡）
# 注意：macOS 內建 /bin/bash 是 3.2，`set -u` 下展開空陣列會噴 unbound variable，
# 所以用長度判斷分兩支呼叫，不能直接寫 "${EXTRA_ARGS[@]}"。
step "全自動製卡"
if [ ${#EXTRA_ARGS[@]} -gt 0 ]; then
  "$PY" mine.py "$VIDEO_ID" --auto --title "$TITLE" "${EXTRA_ARGS[@]}"
else
  "$PY" mine.py "$VIDEO_ID" --auto --title "$TITLE"
fi

# 7) 同步到 AnkiWeb（手機下拉同步即可看到新卡）
step "同步到 AnkiWeb"
"$PY" -c "from anki import invoke; invoke('sync'); print('✓ 已同步')"

echo ""
echo "✓ 完成：$TITLE"
