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
POT_MISSING=0
# 沒裝 PO Token server 又在下載階段失敗，最常見的原因是 YouTube 擋下了下載，這時才提示去安裝。
# 獨立成函式：字幕下載失敗那條路徑是直接 exit 1，不會經過 ERR trap。
pot_hint() {
  if [ "$POT_MISSING" = 1 ]; then
    case "$LAST_STEP" in
      *下載*|*影片網址*|*標題*)
        echo "  若訊息是 Sign in to confirm you're not a bot 這類，是 YouTube 擋下了下載：" >&2
        echo "  請安裝 PO Token server（README「PO Token Server」章節），或換個網路再試。" >&2 ;;
    esac
  fi
}
on_error() {
  echo "" >&2
  echo "✗ 執行失敗：在「$LAST_STEP」這一步中斷（run.sh 第 $1 行）" >&2
  echo "  已完成的下載/卡片不會遺失；修正問題後可直接重新執行同一個指令。" >&2
  pot_hint
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

# 0.5) 確認 PO Token server 有在跑，沒開就自動啟動（YouTube 近期收緊反爬蟲，
#      沒有這個 yt-dlp 常會被擋下載）。找不到已安裝的 server 目錄就只警告，
#      不中斷腳本 —— 有些影片就算沒有 PO token 也可能下載成功。
#      安裝步驟見 README「PO Token Server」章節。
step "確認 PO Token Server"
POT_DIR="$HOME/bgutil-ytdlp-pot-provider/server"
POT_PORT=4416
POT_LOG="$HOME/bgutil-ytdlp-pot-provider/server.log"
pot_ready() {
  (exec 3<>"/dev/tcp/127.0.0.1/$POT_PORT") 2>/dev/null
}
if pot_ready; then
  echo "  PO Token server 已在執行"
elif [ -d "$POT_DIR/build" ]; then
  echo "  啟動 PO Token server..."
  ( cd "$POT_DIR" && nohup node build/main.js > "$POT_LOG" 2>&1 < /dev/null & )
  READY=0
  DEADLINE=$((SECONDS + 15))
  while [ "$SECONDS" -lt "$DEADLINE" ]; do
    if pot_ready; then READY=1; break; fi
    sleep 1
  done
  if [ "$READY" -eq 1 ]; then
    echo "✓ PO Token server 已就緒"
  else
    echo "⚠️ PO Token server 啟動逾時，YouTube 下載可能會被擋（log: $POT_LOG）" >&2
  fi
else
  # 沒裝是常態（多數影片沒有它也能下載），不用警告嚇人；下載真的失敗時，on_error 才提示去安裝
  POT_MISSING=1
  echo "  （未安裝 PO Token server，略過；沒有它多數影片也能下載）"
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

# 4) 下載英文字幕（--write-subs 人工字幕 + --write-auto-subs 自動字幕並用：
#    部分影片如 BBC 只有人工上傳字幕、沒有 en 自動字幕，兩旗標並用時 yt-dlp
#    會優先抓品質較好的人工字幕。若已存在則直接覆蓋，天然可重跑）
#    --sub-lang 不能用精確的 "en"：部分影片（實測多支 BBC 節目）字幕語言標籤是
#    en-GB 這類地區變體，精確比對會找不到字幕而整支失敗。但也不能用 "en.*" 正則
#    ——YouTube 的自動翻譯字幕標籤同樣是 en-XX（en-ar 阿拉伯文、en-zh-TW 中文…），
#    實測一支 TED 影片會一口氣抓 15 種語言，全都用不到，還因為請求太密集被回
#    HTTP 429 而中斷。改成明確列出英語系的地區變體。下載後統一改名成 .en.srt，
#    因為 mine.py 是用固定檔名 {video_id}.en.srt 找字幕的，不知道地區變體這回事。
step "下載字幕"
# 英文字幕的語言標籤。yt-dlp 的 --sub-lang 是把每個項目當正則做完整比對，所以
# 這裡用一個受限的正則而不是 "en.*"：後者會連 en-ar、en-zh-TW 這些「翻譯成其他
# 語言」的版本一起抓（實測一支 TED 影片有 15 種），用不到、拖慢下載，還會觸發
# YouTube 的 HTTP 429 限流而整支中斷。
# 尾巴的 (?:-[A-Za-z0-9]+)* 是為了容納 en-US-x-something 這類帶擴充碼的標籤。
# 代價是沒列到的英文地區碼會抓不到字幕——真遇到時下面的錯誤訊息會提示怎麼查。
EN_LANGS='en(?:-(?:orig|GB|UK|US|CA|AU|NZ|IE|IN|ZA|SG|PH|HK)(?:-[A-Za-z0-9]+)*)?' 
SRT="media/${VIDEO_ID}.en.srt"
# 先清掉舊的 .en*.srt 殘留（不論是先前用純 en 抓到的正規檔名、還是地區變體），
# 保證等一下抓完後 glob 到的一定是這次剛下載的新檔，不會因為舊檔已存在、
# [ ! -s "$SRT" ] 判斷為 false 而漏掉 rename、悄悄用到過期字幕（重跑同一支影片
# 卻拿到舊內容，且不會有任何錯誤訊息，非常隱蔽）。
# .vtt 一起清：轉檔失敗時會留下 .vtt 而沒有 .srt，下面的失敗診斷靠它判斷，
# 不能讓先前殘留的 .vtt 造成誤判。
rm -f "media/${VIDEO_ID}".en*.srt "media/${VIDEO_ID}".en*.vtt
# 一支影片常有多個英文變體（en / en-orig / en-uk），任何一條產生非空的 .srt 就能
# 製卡。成敗一律由下面的檔案檢查判斷，不看 yt-dlp 的退出碼。
# --ignore-errors 不能省：yt-dlp 預設（only_download）遇到第一條字幕下載失敗就
# 拋錯中止這支影片，排在後面的變體根本不會去抓。實測 pMXjmzTV-Jk 的 en 回 429，
# 可以正常下載的 en-orig 因此沒被嘗試，整支失敗。加了才會記警告、繼續抓下一條
# （行為契約見 tests/test_ytdlp_contract.py）。
# 這次呼叫不加 --no-warnings、stderr 另存：加了 -i 之後字幕下載失敗只會記成
# warning，--no-warnings 會把它整個吃掉，失敗時就看不出是 429 還是真的沒字幕。
SUB_ERR_LOG=$(mktemp "${TMPDIR:-/tmp}/ytm-subs.XXXXXX")
"$YTDLP" --no-playlist --ignore-errors --skip-download --write-subs --write-auto-subs \
  --sub-lang "$EN_LANGS" --sub-format srt --convert-subs srt -o "media/%(id)s.%(ext)s" "$URL" \
  2>"$SUB_ERR_LOG" || true
if [ ! -s "$SRT" ]; then
  # 略過空檔：某一條變體可能只產生空檔，要挑第一個「非空」的，不是第一個存在的。
  for CAND in "media/${VIDEO_ID}".en*.srt; do
    if [ -s "$CAND" ]; then
      mv "$CAND" "$SRT"
      break
    fi
  done
fi
if [ ! -s "$SRT" ]; then
  echo "✗ 沒有取得可用的英文字幕（$SRT）" >&2
  # 限流與轉檔失敗可能同時發生（例如 en 被 429、en-orig 下載到了卻轉檔失敗），
  # 各自獨立判斷、兩個都印，不讓其中一個蓋掉另一個。
  DIAGNOSED=0
  if grep -q "HTTP Error 429" "$SUB_ERR_LOG"; then
    echo "  至少一條字幕被 YouTube 限流（HTTP 429，請求太多）。" >&2
    echo "  等幾分鐘再重跑，或換一個網路出口（例如切換 VPN）。" >&2
    DIAGNOSED=1
  fi
  if ls "media/${VIDEO_ID}".en*.vtt >/dev/null 2>&1; then
    echo "  有英文字幕下載到，但轉成 SRT 失敗（留下了 .vtt）。請確認 ffmpeg 可以正常執行。" >&2
    DIAGNOSED=1
  fi
  if [ "$DIAGNOSED" -eq 0 ]; then
    echo "  這支影片可能沒有英文字幕（人工或自動皆無），無法用本工具製卡。" >&2
    echo "  若確定有英文字幕，可能是語言標籤不在預期清單內。用下列指令查看實際標籤，" >&2
    echo "  再把它加進 run.sh 的 EN_LANGS：" >&2
    echo "    yt-dlp --list-subs \"$URL\"" >&2
  fi
  if grep -qE "ERROR|WARNING" "$SUB_ERR_LOG"; then
    echo "  yt-dlp 的原始訊息：" >&2
    grep -E "ERROR|WARNING" "$SUB_ERR_LOG" | tail -5 | sed 's/^/    /' >&2
  fi
  rm -f "$SUB_ERR_LOG"
  pot_hint
  exit 1
fi
# 成功時也把 yt-dlp 的警告印出來：例如 en 被 429、改用 en-orig 成功，
# 使用者應該知道這次拿到的是哪一條、為什麼。
# 只挑字幕下載失敗那一類，其他無關的 yt-dlp 警告不要被誤稱成字幕失敗。
if grep -q "Unable to download video subtitles" "$SUB_ERR_LOG"; then
  echo "  （部分字幕變體沒有下載成功，已改用其他英文字幕）" >&2
  grep "Unable to download video subtitles" "$SUB_ERR_LOG" | tail -5 | sed 's/^/    /' >&2
fi
rm -f "$SUB_ERR_LOG"

# 4.5) 另外抓一份 json3 自動字幕：它每個字都帶時間戳(tOffsetMs)，是 ASR 的實際
#      對齊結果。SRT 只有整段時間、句子邊界得線性內插猜（實測誤差可達 2.6 秒），
#      有 json3 時 mine.py 會優先用它校正時間軸，切出來的音檔準得多。
#      只有自動字幕有這個欄位；純人工字幕的影片抓不到是正常的，不影響製卡。
step "下載逐字時間戳 (json3，可選)"
JSON3="media/${VIDEO_ID}.en.json3"
rm -f "media/${VIDEO_ID}".en*.json3   # 同上，避免悄悄用到重跑前的舊逐字時間戳
"$YTDLP" --no-warnings --no-playlist --ignore-errors --skip-download --write-auto-subs \
  --sub-lang "$EN_LANGS" --sub-format json3 -o "media/%(id)s.%(ext)s" "$URL" >/dev/null 2>&1 || true
if [ ! -s "$JSON3" ]; then
  FOUND_JSON3=$(ls "media/${VIDEO_ID}".en*.json3 2>/dev/null | head -1 || true)
  if [ -n "$FOUND_JSON3" ]; then
    mv "$FOUND_JSON3" "$JSON3"
  fi
fi
if [ -s "$JSON3" ]; then
  echo "  ✓ 已取得逐字時間戳"
else
  echo "  – 沒有取得逐字時間戳，改用 SRT 內插時間（不影響製卡，但音檔切點可能較不準）"
fi

# 5) 下載 360p 影片（做音檔/截圖用；若已存在會被覆蓋，可重跑）
# yt-dlp 預設會挑一個 player client（常自動選到 android_vr），近期實測這個 client
# 常被 YouTube 回 403 擋下，但同一支影片換成明確指定 android client 立刻就能下載成功
# （已連續在多支影片上驗證），所以先跑預設，失敗了就自動retry一次、明確指定 android
# client，省去每次都要手動介入重試。
step "下載影片 (360p)"
if ! "$YTDLP" --no-warnings --no-playlist -f "best[height<=360]/bestvideo[height<=360]+bestaudio/best" \
  --merge-output-format mp4 -o "media/%(id)s.%(ext)s" "$URL"; then
  echo "  預設 client 下載失敗（常見原因：YouTube 暫時擋下 android_vr client），改用 android client 重試..." >&2
  "$YTDLP" --no-warnings --no-playlist --extractor-args "youtube:player_client=android" \
    -f "best[height<=360]/bestvideo[height<=360]+bestaudio/best" \
    --merge-output-format mp4 -o "media/%(id)s.%(ext)s" "$URL"
fi

# 6) 全自動挑字製卡（已挖過的字會被去重跳過，可重跑不會重複灌卡）
# 注意：macOS 內建 /bin/bash 是 3.2，`set -u` 下展開空陣列會噴 unbound variable，
# 所以用長度判斷分兩支呼叫，不能直接寫 "${EXTRA_ARGS[@]}"。
# VIDEO_ID 放最後且前面加 `--`：YouTube ID 偶爾以 - 開頭（如 -JJ4OE0rZJo），
# argparse 若把它排在其他選項之前會誤判成未知旗標而整個報錯；`--` 只讓它之後的
# token 停止當選項解析，所以其他旗標都要留在 `--` 之前。
step "全自動製卡"
if [ ${#EXTRA_ARGS[@]} -gt 0 ]; then
  "$PY" mine.py --auto --title "$TITLE" "${EXTRA_ARGS[@]}" -- "$VIDEO_ID"
else
  "$PY" mine.py --auto --title "$TITLE" -- "$VIDEO_ID"
fi

# 7) 同步到 AnkiWeb（手機下拉同步即可看到新卡）
#    用 anki.sync() 而不是直接 invoke('sync')：新版 AnkiConnect 送出就回傳、背景才同步，
#    不等它結束的話，下一步 monkeytype 讀牌組會被拒。
#    卡片到這裡已經建好，同步失敗或等太久只警告、不中斷，免得把後面的步驟一起擋掉
#    沒登入 AnkiWeb（只用桌面版）不是錯誤，anki.py 會印一行說明就結束
step "同步到 AnkiWeb"
"$PY" anki.py sync || true

# 8) 同步卡片到 monkeytype 打字練習（選配整合，會改寫相鄰 ../monkeytype 專案的
#    資料檔。「修改另一個專案」不該是一鍵製卡的隱含副作用，所以預設關閉：
#    要用的人在 .env 加一行 SYNC_MONKEYTYPE=1 顯式啟用。失敗只警告，不影響製卡結果。
#    沒啟用的人整段都不顯示：他們不知道 monkeytype 是什麼，看到「略過」只會困惑）
if grep -qx 'SYNC_MONKEYTYPE=1' .env 2>/dev/null; then
  step "同步到 monkeytype"
  if [ -f "sync_monkeytype.py" ] && [ -d "../monkeytype" ]; then
    if "$PY" sync_monkeytype.py; then
      echo "✓ 打字練習內容已更新（重新整理 monkeytype 頁面即生效）"
    else
      echo "⚠️ monkeytype 同步失敗（卡片已做完不受影響），可稍後手動執行：$PY sync_monkeytype.py" >&2
    fi
  elif [ ! -f "sync_monkeytype.py" ]; then
    echo "  （略過：找不到 sync_monkeytype.py）"
  else
    echo "  （略過：找不到 ../monkeytype 專案）"
  fi
fi

echo ""
echo "✓ 完成：$TITLE"
