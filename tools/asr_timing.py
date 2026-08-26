"""用本機語音辨識產生逐字時間戳，補上 YouTube 沒提供的部分。

為什麼需要：管線在有 json3 逐字時間戳時能精準定位句子邊界，沒有時只能拿 SRT 的
段落時間做線性內插。而**人工上傳字幕的影片沒有逐字時間戳**（json3 裡每個片段是
一整行字），這類影片在本專案佔約 54% 的卡片，且字幕時間軸本身還可能與音訊不同步
（見 issue #1，實測有偏移 0.62 秒的案例）——內插再怎麼算都跟著偏。

這支工具直接從音訊產生逐字時間，不依賴字幕的時間標示，所以兩個問題一起解決。
輸出格式與 mine.parse_json3() 相同（[(字, 起, 迄), ...]），可直接餵給
mine.locate_sentence() / refine_with_json3() 那套既有邏輯。

## 執行環境

需要 openai-whisper。注意版本組合：實測 Python 3.13 + torch 2.13 跑
--word_timestamps 會 segfault（OMP libomp.dylib 重複初始化），Python 3.10 +
torch 2.3.1 則正常。所以預設用獨立的直譯器路徑，不裝進專案的 .venv，避免污染
主管線的相依（一般使用者完全不需要裝這個）。

用 --whisper-python 指定可用的直譯器，或設環境變數 ASR_PYTHON。

用法：
    python tools/asr_timing.py media/VIDEO.mp4 -o media/VIDEO.asr.json
    python tools/asr_timing.py media/VIDEO.mp4 --model small
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_PYTHON = os.environ.get(
    "ASR_PYTHON",
    "/opt/homebrew/Caskroom/miniforge/base/envs/whisper/bin/python",
)

# whisper 逐字時間戳跑在需要 OpenMP 的路徑上，macOS 常同時載入多份 libomp。
# 這個變數讓它容忍重複載入；不設的話部分環境會直接中止。
ASR_ENV = {**os.environ, "KMP_DUPLICATE_LIB_OK": "TRUE"}


def extract_wav(media_path, out_wav):
    """抽出 16kHz 單聲道 wav——whisper 內部本來就會轉成這個格式，先轉好省一次。"""
    subprocess.run(
        ["ffmpeg", "-y", "-i", media_path, "-vn", "-ar", "16000", "-ac", "1", out_wav],
        check=True, capture_output=True,
    )


def transcribe(wav_path, model="base", python_bin=DEFAULT_PYTHON, language="en"):
    """跑 whisper 並回傳解析後的結果 dict。"""
    if not os.path.exists(python_bin):
        sys.exit(
            f"找不到可用的 whisper 直譯器：{python_bin}\n"
            "請用 --whisper-python 指定，或設環境變數 ASR_PYTHON。\n"
            "注意 Python 3.13 + torch 2.13 跑 --word_timestamps 會 segfault，"
            "需要較舊的組合（實測 Python 3.10 + torch 2.3.1 正常）。"
        )
    with tempfile.TemporaryDirectory() as td:
        cmd = [
            python_bin, "-m", "whisper", wav_path,
            "--model", model, "--language", language,
            "--output_format", "json", "--output_dir", td,
            "--fp16", "False", "--word_timestamps", "True",
        ]
        p = subprocess.run(cmd, capture_output=True, text=True, env=ASR_ENV)
        out = os.path.join(td, os.path.basename(wav_path).rsplit(".", 1)[0] + ".json")
        if p.returncode != 0 or not os.path.exists(out):
            sys.exit(f"whisper 執行失敗（exit {p.returncode}）：\n{p.stderr[-1500:]}")
        return json.load(open(out, encoding="utf-8"))


def to_word_list(result):
    """把 whisper 的輸出轉成 mine.parse_json3() 的格式：[(字, 起, 迄), ...]。

    whisper 的字會帶前導空白（" the"），且標點是黏在字尾的（"emissions."）——
    這兩點跟 json3 的形態一致，mine._norm_word() 本來就會處理，所以只做去空白。
    """
    words = []
    for seg in result.get("segments", []):
        for w in seg.get("words", []):
            txt = (w.get("word") or "").strip()
            if not txt:
                continue
            st, en = float(w.get("start", 0.0)), float(w.get("end", 0.0))
            words.append((txt, st, max(en, st + 0.05)))
    words.sort(key=lambda x: x[1])
    return words


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("media", help="影片或音檔路徑")
    ap.add_argument("-o", "--out", help="輸出 JSON 路徑（預設與輸入同名 .asr.json）")
    ap.add_argument("--model", default="base",
                    help="whisper 模型；base 約 14 秒/分鐘音訊，small 約 41 秒/分鐘")
    ap.add_argument("--language", default="en")
    ap.add_argument("--whisper-python", default=DEFAULT_PYTHON,
                    help="裝有 openai-whisper 的直譯器路徑")
    args = ap.parse_args()

    out = args.out or (args.media.rsplit(".", 1)[0] + ".asr.json")
    with tempfile.TemporaryDirectory() as td:
        wav = os.path.join(td, "audio.wav")
        print(f"抽取音訊：{args.media}")
        extract_wav(args.media, wav)
        print(f"語音辨識中（model={args.model}）...")
        result = transcribe(wav, args.model, args.whisper_python, args.language)

    words = to_word_list(result)
    if not words:
        sys.exit("辨識結果沒有逐字時間戳——確認 whisper 版本支援 --word_timestamps。")
    json.dump([{"w": w, "s": round(s, 3), "e": round(e, 3)} for w, s, e in words],
              open(out, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"✓ {len(words)} 個逐字時間戳 → {out}")


def load_word_list(path):
    """讀回本工具產生的檔案，格式與 mine.parse_json3() 相同。"""
    data = json.load(open(path, encoding="utf-8"))
    return [(d["w"], d["s"], d["e"]) for d in data]


if __name__ == "__main__":
    main()
