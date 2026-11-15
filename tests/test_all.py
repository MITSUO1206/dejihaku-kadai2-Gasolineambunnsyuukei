"""テスト一式:  .venv\\Scripts\\python -m pytest -q"""
from __future__ import annotations

import copy
import json
import random
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.allocator import split_amount  # noqa: E402
from src.common import load_config, normalize_card_no, parse_amount  # noqa: E402
from src.pdf_reader import parse_text  # noqa: E402
from src.pipeline import run, save_correction  # noqa: E402
from tools.make_dummy import make_dummy  # noqa: E402

MONTH = "2026-10"


# ---- 部品 -------------------------------------------------------------------
def test_normalize():
    assert normalize_card_no("４９８０－１２３４ 5678-0001") == "4980123456780001"
    assert parse_amount("¥１２，３４５円") == 12345
    assert parse_amount("¥ 7,0 37") == 7037 or parse_amount("¥ 7,0 37") is None  # 空白は許容しても良い
    assert parse_amount("abc") is None


@pytest.mark.parametrize("amount,ratios,expected", [
    (10000, [33.33, 33.33, 33.34], [3333, 3333, 3334]),
    (100, [50, 50], [50, 50]),
    (101, [50, 50], [51, 50]),          # 端数が同じなら先の行へ
    (1, [33.33, 33.33, 33.34], [0, 0, 1]),
    (0, [60, 40], [0, 0]),
])
def test_split_amount_examples(amount, ratios, expected):
    assert split_amount(amount, ratios) == expected


def test_split_amount_total_always_matches():
    rng = random.Random(0)
    for _ in range(2000):
        n = rng.randint(1, 8)
        cuts = sorted(rng.sample(range(1, 10000), n - 1))
        ratios = [(b - a) / 100 for a, b in zip([0] + cuts, cuts + [10000])]
        amount = rng.randint(0, 500000)
        parts = split_amount(amount, ratios)
        assert sum(parts) == amount
        assert all(p >= 0 for p in parts)


def test_parse_text_marks_unreadable():
    st = parse_text(["ご請求合計金額 ¥1,500",
                     "カード番号 4980-0000-0000-0001 名義 テスト 1",
                     "10/01 SS本町 レギュラー 5.00L 1,000",
                     "10/02 SS本町 レギュラー ５．００L ¥ 5 00",
                     "カード小計 1,500"])
    c = st.cards["4980000000000001"]
    assert c.amount == 1000 and c.subtotal == 1500
    assert len(c.unreadable) == 1


# ---- 全体（ダミーデータ） ----------------------------------------------------------
@pytest.fixture
def env(tmp_path):
    cfg = copy.deepcopy(load_config())
    for k in cfg["paths"]:
        cfg["paths"][k] = str(tmp_path / k)
    data = make_dummy(Path(cfg["paths"]["input_dir"]) / MONTH, MONTH)
    return cfg, data


def codes(res, severity=None):
    return sorted({i.code for i in res.issues if severity is None or i.severity == severity})


def test_detects_planted_errors(env):
    cfg, data = env
    res = run(MONTH, cfg, use_ai=False)
    a = data["answer"]
    assert codes(res, "エラー") == ["A001", "A002", "L003", "M001", "M002", "P001"]
    by = {(i.code, i.card_no) for i in res.issues}
    for no in a["unregistered_cards"]:
        assert ("M001", normalize_card_no(no)) in by
    assert ("M002", normalize_card_no(a["expired_card"])) in by
    assert ("M003", normalize_card_no(a["holder_mismatch_card"])) in by
    assert ("P001", normalize_card_no(a["garbled_card"])) in by
    assert not res.can_export and "csv" not in res.outputs


def test_card_change_uses_latest_ledger_row(env):
    cfg, data = env
    res = run(MONTH, cfg, use_ai=False, write=False)
    moved = [r for r in data["ledger"] if r["備考"].endswith("から移管")]
    assert len(moved) == 3
    for r in moved:
        row = res.cards[res.cards["card_no"] == normalize_card_no(r["カード番号"])]
        if len(row):
            assert row.iloc[0]["facility_code"] == r["施設コード"]


def _fake_client(amount_for):
    """Claude API の代わり。amount_for(text) が返す金額で応答する"""
    def create(**kw):
        lines = [ln.split(": ", 1) for ln in kw["messages"][0]["content"].splitlines()[1:]]
        out = [{"line_id": lid, "is_usage_line": True, "date": "10/10", "station": "SS", "item": "レギュラー",
                "liters": 41.15, "amount": amount_for(t), "note": "テスト"} for lid, t in lines]
        return SimpleNamespace(stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text=json.dumps({"lines": out}))])
    return SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))


def test_ai_repair_accepted_when_subtotal_matches(env):
    cfg, data = env
    res = run(MONTH, cfg, use_ai=True, ai_client=_fake_client(lambda t: data["answer"]["garbled_amount"]))
    assert "P001" not in codes(res)
    p5 = [i for i in res.issues if i.code == "P005"]
    assert len(p5) == 1 and p5[0].amount == data["answer"]["garbled_amount"]
    sent = json.dumps(res.statement.ai_log[0]["sent"], ensure_ascii=False)
    assert "4980" not in sent  # カード番号はAIに送っていない


def test_ai_repair_rejected_when_subtotal_differs(env):
    cfg, _ = env
    res = run(MONTH, cfg, use_ai=True, ai_client=_fake_client(lambda t: 1))
    assert "P001" in codes(res) and "P005" not in codes(res)


def test_ai_failure_falls_back(env):
    cfg, _ = env
    def boom(**kw):
        raise ConnectionError("network down")
    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=boom)))
    res = run(MONTH, cfg, use_ai=True, ai_client=client)
    assert "P001" in codes(res)
    assert "error" in res.statement.ai_log[0]


def test_fix_all_then_csv_matches(env):
    cfg, data = env
    res = run(MONTH, cfg, use_ai=False)
    for i in [i for i in res.issues if i.severity == "エラー"]:
        if i.code == "P001":
            save_correction(cfg, MONTH, "pdf_overrides", i.card_no, {"amount": i.ref["subtotal"]}, "test")
        elif i.code in ("M001", "M002", "L003"):
            save_correction(cfg, MONTH, "card_overrides", i.card_no,
                            {"facility_code": "F001", "category": "施設", "user_name": i.user}, "test")
        elif i.code in ("A001", "A002"):
            save_correction(cfg, MONTH, "alloc_overrides", i.ref["employee_no"],
                            [{"facility_code": "F001", "ratio": 70}, {"facility_code": "F002", "ratio": 30}],
                            "test")
    res = run(MONTH, cfg, use_ai=False)  # 再集計
    assert res.error_count == 0, [i.to_row() for i in res.issues if i.severity == "エラー"]
    assert res.totals_ok and res.can_export
    csv = pd.read_csv(res.outputs["csv"], encoding="cp932")
    total = data["answer"]["grand_total"]
    assert csv["借方金額"].sum() == csv["貸方金額"].sum() == total
    assert res.totals["按分後合計"] == res.totals["明細読取合計"] == total
    xl = pd.read_excel(res.outputs["summary"], sheet_name="施設別集計")
    assert int(xl[xl["施設コード"] == "合計"]["合計金額"].iloc[0]) == total
