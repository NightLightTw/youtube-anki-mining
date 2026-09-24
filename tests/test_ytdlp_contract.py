"""run.sh 依賴的 yt-dlp 行為契約。

run.sh 一次請求多個英文字幕變體（en / en-orig / en-GB…），只要其中一條抓到就能製卡。
這仰賴一個 yt-dlp 的內部行為：加了 --ignore-errors 之後，某一條字幕下載失敗會記警告、
繼續抓下一條；預設（only_download）則是第一條失敗就拋錯，後面的變體根本不會被嘗試。

實測 pMXjmzTV-Jk 的 en 回 HTTP 429 時，可以正常下載的 en-orig 就是因為預設行為而沒被
嘗試，整支失敗。這個行為只有在 YouTube 真的限流時才會出現，無法靠實際下載穩定重現，
所以用 mock 讓第一條字幕拋錯，不發任何網路請求。

測到的是 yt-dlp 的私有方法 _write_subtitles。升級 yt-dlp 後若這裡壞掉，代表內部結構
變了，要重新確認 run.sh 的 --ignore-errors 是否仍有同樣效果，而不是直接改測試讓它過。
"""
import pathlib
import re

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError


def _write_two_variants(tmp_path, ignoreerrors):
    """請求 en 與 en-orig 兩條字幕，en 一律回 429，回傳 (實際嘗試過的語言, 回傳值或例外)。"""
    ydl = YoutubeDL({
        "ignoreerrors": ignoreerrors,
        "writeautomaticsub": True,
        "quiet": True,
        "no_warnings": True,
        "outtmpl": str(tmp_path / "%(id)s.%(ext)s"),
    })
    tried = []

    def fake_dl(name, info, subtitle=False, test=False):
        tried.append(info["_lang"])
        if info["_lang"] == "en":
            raise DownloadError("HTTP Error 429: Too Many Requests")
        (tmp_path / name).write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")

    ydl.dl = fake_dl
    info = {
        "id": "vid", "ext": "mp4", "title": "t",
        "requested_subtitles": {
            "en": {"ext": "srt", "url": "http://invalid/en", "_lang": "en"},
            "en-orig": {"ext": "srt", "url": "http://invalid/en-orig", "_lang": "en-orig"},
        },
    }
    try:
        result = ydl._write_subtitles(info, str(tmp_path / "vid.mp4"))
    except DownloadError as err:
        result = err
    return tried, result


def test_default_mode_stops_at_the_first_failed_subtitle(tmp_path):
    tried, result = _write_two_variants(tmp_path, "only_download")
    assert tried == ["en"]
    assert isinstance(result, DownloadError)


def test_ignore_errors_goes_on_to_the_next_subtitle_variant(tmp_path):
    tried, result = _write_two_variants(tmp_path, True)
    assert tried == ["en", "en-orig"]
    assert [final for _, final in result] == [str(tmp_path / "vid.en-orig.srt")]


def test_every_subtitle_download_in_run_sh_ignores_errors():
    """上面兩個測試只證明 yt-dlp 的行為；這裡確認 run.sh 真的用了那個旗標。"""
    script = (pathlib.Path(__file__).resolve().parent.parent / "run.sh").read_text(encoding="utf-8")
    # 把反斜線續行接回同一行，每個元素就是一個完整的指令
    commands = re.sub(r"\\\n\s*", " ", script).splitlines()
    sub_downloads = [c for c in commands if '"$YTDLP"' in c and "--sub-lang" in c]
    assert len(sub_downloads) == 2  # srt 與 json3 各一次；數量變了代表這裡要跟著檢查
    for cmd in sub_downloads:
        assert "--ignore-errors" in cmd, cmd
