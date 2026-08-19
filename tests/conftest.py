"""讓測試能直接 import 專案根目錄的模組（autopick / mine）。"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
