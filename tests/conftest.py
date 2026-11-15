"""テスト共通：本物の data/app.db を触らないよう、テストごとに一時DBへ切り替える"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import db  # noqa: E402


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("GASCARD_DEMO", "0")  # 通常（実データ用）モードでテストする
    original = db.db_path()
    db.set_path(tmp_path / "test.db")
    yield
    db.set_path(original)
