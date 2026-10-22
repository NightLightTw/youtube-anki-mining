"""讓測試能直接 import 專案根目錄的模組（autopick / mine）。"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_history(tmp_path, monkeypatch):
    """測試建卡時不能把紀錄寫進 repo 的 history/。
    預設「沒啟用」，跟一般使用者一樣；需要記錄的測試（test_history.py）自己明確啟用。"""
    import history
    monkeypatch.setattr(history, "HISTORY_DIR", tmp_path / "history")
    monkeypatch.delenv("RECORD_HISTORY", raising=False)
