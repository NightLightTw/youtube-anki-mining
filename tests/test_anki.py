"""anki.sync() 的回歸測試：新版 AnkiConnect 的 sync 送出就回傳，要等背景同步結束。"""
import pathlib

import pytest

import anki

BUSY = "AnkiConnect 錯誤（deckNames）：Collection lifecycle operation is active."


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr(anki.time, "monotonic", fake.monotonic)
    monkeypatch.setattr(anki.time, "sleep", fake.sleep)
    return fake


MAX_POLLS = 100


def _fake_invoke(monkeypatch, busy_polls, poll_error=BUSY):
    """sync 之後的前 busy_polls 次 deckNames 回忙碌錯誤，之後成功。

    輪詢超過 MAX_POLLS 次直接判失敗：拿掉逾時判斷時，測試要立刻失敗而不是空轉。
    """
    calls = []

    def fake(action, **params):
        calls.append(action)
        if action == "sync":
            return None
        assert action == "deckNames"
        polls = len(calls) - 1
        assert polls <= MAX_POLLS, "輪詢沒有在逾時後停下"
        if polls <= busy_polls:
            raise RuntimeError(poll_error)
        return ["YouTube Mining"]

    monkeypatch.setattr(anki, "invoke", fake)
    return calls


def test_waits_until_background_sync_finishes(monkeypatch, clock):
    calls = _fake_invoke(monkeypatch, busy_polls=3)
    anki.sync(poll=0.5)
    assert calls == ["sync"] + ["deckNames"] * 4
    assert clock.sleeps == [0.5, 0.5, 0.5]


def test_returns_at_once_when_sync_already_finished(monkeypatch, clock):
    # 舊版 AnkiConnect 跑完同步才回傳，第一次輪詢就成功
    calls = _fake_invoke(monkeypatch, busy_polls=0)
    anki.sync()
    assert calls == ["sync", "deckNames"]
    assert clock.sleeps == []


def test_other_errors_are_not_mistaken_for_busy(monkeypatch, clock):
    _fake_invoke(monkeypatch, busy_polls=1, poll_error="連不上 AnkiConnect")
    with pytest.raises(RuntimeError, match="連不上 AnkiConnect"):
        anki.sync()
    assert clock.sleeps == []


def test_gives_up_after_timeout(monkeypatch, clock):
    calls = _fake_invoke(monkeypatch, busy_polls=MAX_POLLS)
    with pytest.raises(RuntimeError, match="超過 5 秒"):
        anki.sync(timeout=5, poll=1)
    assert calls.count("sync") == 1
    assert clock.now == 5


def test_failed_sync_request_is_not_polled(monkeypatch, clock):
    calls = []

    def fake(action, **params):
        calls.append(action)
        raise RuntimeError("AnkiConnect 錯誤（sync）：sync: auth not configured")

    monkeypatch.setattr(anki, "invoke", fake)
    with pytest.raises(RuntimeError, match="auth not configured"):
        anki.sync()
    assert calls == ["sync"]


def test_run_sh_waits_for_sync_before_monkeytype():
    lines = (pathlib.Path(__file__).resolve().parent.parent / "run.sh").read_text().splitlines()
    code = "\n".join(line for line in lines if not line.lstrip().startswith("#"))
    assert "from anki import sync; sync()" in code
    # 卡片已經建好，同步沒確認完成只警告，不能讓 set -e 中斷後面的步驟
    assert 'if ! "$PY" -c "from anki import sync; sync()' in code
    assert "invoke('sync')" not in code
    assert code.index("sync()") < code.index("sync_monkeytype.py")
