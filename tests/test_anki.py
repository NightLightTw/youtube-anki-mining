"""anki.sync() 的回歸測試：新版 AnkiConnect 的 sync 送出就回傳，要等背景同步結束。"""
import pathlib
import urllib.error

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


# ---------- 一般使用者的負擔：不該看到跟自己無關的訊息 ----------

import io
import json
import runpy
import subprocess
import sys
import textwrap


def _cli(monkeypatch, capsys, responses):
    """用假的 AnkiConnect 回應執行 `python anki.py sync`，回傳 (結束碼, stdout, stderr)。
    responses：依序回傳的 JSON，或要丟出的例外。"""
    queue = list(responses)

    class Resp:
        def __init__(self, body): self.body = body
        def read(self): return json.dumps(self.body).encode()

    def fake_urlopen(*a, **k):
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return Resp(item)

    monkeypatch.setattr(sys, "argv", ["anki.py", "sync"])
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(SystemExit) as ex:
        runpy.run_path(str(pathlib.Path(anki.__file__)), run_name="__main__")
    out = capsys.readouterr()
    return ex.value.code, out.out, out.err


def test_cli_without_ankiweb_login_exits_zero_with_one_quiet_line(monkeypatch, capsys):
    """只用桌面版 Anki、沒有 AnkiWeb 帳號的人：不是出錯，也不該被叫去「按同步」。"""
    code, out, err = _cli(monkeypatch, capsys,
                          [{"result": None, "error": "sync: auth not configured"}])
    assert code == 0 and err == ""
    assert out.strip() == "（未登入 AnkiWeb，略過同步；卡片已保存在本機）"


def test_cli_success_goes_to_stdout_and_does_not_claim_more_than_it_knows(monkeypatch, capsys):
    code, out, err = _cli(monkeypatch, capsys, [{"result": None, "error": None},
                                                  {"result": ["YouTube Mining"], "error": None}])
    assert code == 0 and err == ""
    assert "已送出 AnkiWeb 同步" in out and "已同步" not in out      # 外掛不告訴我們背景同步成功與否


def test_cli_failure_goes_to_stderr_without_a_traceback(monkeypatch, capsys):
    code, out, err = _cli(monkeypatch, capsys, [urllib.error.URLError("refused")])
    assert code == 1 and out == ""
    assert "Traceback" not in err and "AnkiWeb 同步沒有確認完成" in err and "按右上角" in err


@pytest.mark.parametrize("error", [
    "authenticationRequired: invalid credentials",      # 帳密過期：是真的失敗，不能當成「沒登入」略過
    "sync: auth failed",
    "AnkiConnect error",
])
def test_other_failures_are_never_mistaken_for_no_login(monkeypatch, capsys, error):
    code, out, err = _cli(monkeypatch, capsys, [{"result": None, "error": error}])
    assert code == 1 and "按右上角" in err and "略過同步" not in out + err


def test_cli_wires_up_the_long_wait_reminder_once_and_flushes_it(monkeypatch, capsys, clock):
    """整個 CLI 跑一次：同步卡在忙碌狀態 100 秒。提醒要印一次、要 flush（否則使用者看不到）。"""
    class Resp:
        def __init__(self, body): self.body = body
        def read(self): return json.dumps(self.body).encode()

    def fake_urlopen(req, *a, **k):
        action = json.loads(req.data)["action"]
        if action == "sync":
            return Resp({"result": None, "error": None})
        if clock.now < 100:
            return Resp({"result": None, "error": "Collection lifecycle operation is active."})
        return Resp({"result": ["YouTube Mining"], "error": None})

    printed = []
    real_print = print

    def spy_print(*args, **kw):
        printed.append((args, kw.get("flush", False)))
        real_print(*args, **kw)

    monkeypatch.setattr(sys, "argv", ["anki.py", "sync"])
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.monotonic", clock.monotonic)
    monkeypatch.setattr("time.sleep", clock.sleep)
    monkeypatch.setattr("builtins.print", spy_print)
    with pytest.raises(SystemExit) as ex:
        runpy.run_path(str(pathlib.Path(anki.__file__)), run_name="__main__")
    out = capsys.readouterr().out
    assert ex.value.code == 0 and out.count("上傳或下載") == 1 and "已送出 AnkiWeb 同步" in out
    reminder = [flush for args, flush in printed if "上傳或下載" in args[0]]
    assert reminder == [True]                                    # 印出去的當下就要看得到


def test_long_wait_prints_one_reminder_about_the_choice_dialog(monkeypatch, clock):
    monkeypatch.setattr(anki, "invoke", lambda action, **kw: None if action == "sync" else
                        (_ for _ in ()).throw(RuntimeError(BUSY)) if clock.now < 100 else ["d"])
    notices = []
    anki.sync(poll=1, notice_after=30, on_notice=lambda: notices.append(clock.now))
    assert len(notices) == 1 and 30 <= notices[0] < 40          # 只提醒一次，而且是等了一陣子才提醒


def test_fast_sync_never_prints_the_reminder(monkeypatch, clock):
    monkeypatch.setattr(anki, "invoke", lambda action, **kw: None if action == "sync" else ["d"])
    notices = []
    anki.sync(on_notice=lambda: notices.append(1))
    assert notices == []


# ---------- 實際執行 run.sh 裡的 shell 片段 ----------

RUN_SH = (pathlib.Path(__file__).resolve().parent.parent / "run.sh").read_text()


def _bash(script, cwd, env=None):
    p = subprocess.run(["bash", "-c", script], cwd=cwd, capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin", **(env or {})}, timeout=30)
    return p.returncode, p.stdout, p.stderr


def _monkeytype_block():
    start = RUN_SH.index("if grep -qx 'SYNC_MONKEYTYPE=1' .env")
    return RUN_SH[start:RUN_SH.index('echo ""\necho "✓ 完成', start)]


def _run_monkeytype(tmp_path, dotenv, with_script=True, with_project=True, stub_exit=0):
    """PY 換成會把收到的參數記下來的假指令，才能證明 sync_monkeytype.py 真的被呼叫。"""
    work = tmp_path / "w"
    work.mkdir(parents=True)
    if dotenv is not None:
        (work / ".env").write_text(dotenv)
    if with_script:
        (work / "sync_monkeytype.py").write_text("")
    if with_project:
        (tmp_path / "monkeytype").mkdir()
    stub = tmp_path / "stub.sh"
    stub.write_text(f'#!/bin/bash\necho "$@" >> "{tmp_path}/calls.log"\nexit {stub_exit}\n')
    stub.chmod(0o755)
    script = f'set -euo pipefail\nstep() {{ echo "== $1 =="; }}\nPY="{stub}"\n' + _monkeytype_block()
    return _bash(script, work)


def _calls(tmp_path):
    log = tmp_path / "calls.log"
    return log.read_text().split("\n")[:-1] if log.exists() else []


@pytest.mark.parametrize("dotenv", [None, "", "MW_LEARNERS_KEY=x\n", "SYNC_MONKEYTYPE=0\n",
                                    "SYNC_MONKEYTYPE=\n", "# SYNC_MONKEYTYPE=1\n"])
def test_monkeytype_prints_nothing_at_all_unless_enabled(tmp_path, dotenv):
    code, out, err = _run_monkeytype(tmp_path, dotenv)
    assert code == 0 and out == "" and err == ""                # 沒有標題、沒有「略過」
    assert _calls(tmp_path) == []                               # 也沒有真的去執行


def test_monkeytype_runs_and_reports_when_enabled(tmp_path):
    code, out, err = _run_monkeytype(tmp_path, "SYNC_MONKEYTYPE=1\n")
    assert code == 0 and "== 同步到 monkeytype ==" in out and "打字練習內容已更新" in out
    assert _calls(tmp_path) == ["sync_monkeytype.py"]           # 真的呼叫了


def test_monkeytype_failure_only_warns_and_never_aborts_the_run(tmp_path):
    code, out, err = _run_monkeytype(tmp_path, "SYNC_MONKEYTYPE=1\n", stub_exit=1)
    assert code == 0 and "monkeytype 同步失敗" in err and "打字練習內容已更新" not in out
    assert _calls(tmp_path) == ["sync_monkeytype.py"]


def test_monkeytype_is_not_run_when_a_prerequisite_is_missing(tmp_path):
    _run_monkeytype(tmp_path / "a", "SYNC_MONKEYTYPE=1\n", with_script=False)
    _run_monkeytype(tmp_path / "b", "SYNC_MONKEYTYPE=1\n", with_project=False)
    assert _calls(tmp_path / "a") == [] and _calls(tmp_path / "b") == []


def test_monkeytype_names_the_missing_prerequisite(tmp_path):
    _, out, _ = _run_monkeytype(tmp_path / "a", "SYNC_MONKEYTYPE=1\n", with_script=False)
    assert "找不到 sync_monkeytype.py" in out
    _, out, _ = _run_monkeytype(tmp_path / "b", "SYNC_MONKEYTYPE=1\n", with_project=False)
    assert "找不到 ../monkeytype 專案" in out


def _pot_hint(step, missing):
    start = RUN_SH.index("pot_hint() {")
    fn = RUN_SH[start:RUN_SH.index("\non_error() {", start)]
    return _bash(f'set -euo pipefail\nLAST_STEP="{step}"\nPOT_MISSING={missing}\n{fn}\npot_hint', ".")


def _step_names():
    import re
    return re.findall(r'^\s*step "([^"]+)"', RUN_SH, flags=re.M)


def test_pot_hint_appears_only_for_download_steps_when_the_server_is_missing():
    steps = _step_names()
    assert "下載字幕" in steps and "全自動製卡" in steps          # 抓到的是 run.sh 真正的步驟名稱
    shown = {s for s in steps if "PO Token server" in _pot_hint(s, 1)[2]}
    assert {"取得影片標題", "下載字幕", "下載影片 (360p)", "解析影片網址"} <= shown
    assert not shown & {"全自動製卡", "同步到 AnkiWeb", "檢查環境", "確認 Anki / AnkiConnect"}


def test_pot_hint_is_silent_when_the_server_is_installed():
    for s in _step_names():
        assert _pot_hint(s, 0) == (0, "", "")


def test_subtitle_failure_path_shows_the_hint_even_though_it_exits_directly():
    """字幕下載失敗是直接 exit 1，不會走 ERR trap，所以要明確呼叫。"""
    start = RUN_SH.index('echo "✗ 沒有取得可用的英文字幕')
    failure = RUN_SH[start:RUN_SH.index("exit 1", start)]
    assert "pot_hint" in failure


def test_error_handler_survives_set_u_before_the_pot_check_has_run():
    """run.sh 開著 set -u：如果 POT_MISSING 還沒定義就出錯（例如前置檢查失敗），
    錯誤處理器自己會再炸一次，把真正的錯誤訊息蓋掉。這裡用 run.sh 開頭真正的宣告與函式來跑。"""
    head = RUN_SH[RUN_SH.index("set -euo pipefail"):RUN_SH.index('step "切換到專案目錄"')]
    code, out, err = _bash(head + '\nLAST_STEP="檢查環境"\nfalse\n', ".")
    assert "unbound variable" not in err and "執行失敗：在「檢查環境」這一步中斷" in err


def test_run_sh_waits_for_sync_before_monkeytype():
    code = "\n".join(l for l in RUN_SH.splitlines() if not l.lstrip().startswith("#"))
    assert '"$PY" anki.py sync || true' in code               # 沒確認完成只警告，不能中斷後面的步驟
    assert code.index("anki.py sync") < code.index("sync_monkeytype.py")
