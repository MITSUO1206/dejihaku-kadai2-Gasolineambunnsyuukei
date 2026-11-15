"""ログイン・ユーザー管理・設定・操作ログ・月次集計（SQL）のテスト"""
from __future__ import annotations

import copy
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from src import auth, db, reports, settings
from src.common import load_config
from src.pipeline import run, save_correction
from tools.make_dummy import make_dummy

PW = "Passw0rd!"


def admin():
    auth.create_first_admin("admin", "管理 太郎", PW)


# ---- パスワード・ログイン ------------------------------------------------------------
def test_password_is_hashed_not_stored_plain():
    admin()
    with db.connect() as con:
        h = con.execute("SELECT password_hash FROM users WHERE username='admin'").fetchone()[0]
    assert PW not in h and h.startswith("$2")


def test_login_success_and_failure_logged():
    admin()
    assert auth.authenticate("admin", PW)["role"] == "admin"
    with pytest.raises(auth.AuthError):
        auth.authenticate("admin", "wrong-pass1")
    with pytest.raises(auth.AuthError):
        auth.authenticate("nobody", PW)
    actions = [r["action"] for r in db.recent_logs()]
    assert actions.count("ログイン失敗") == 2 and "ログイン" in actions


def test_lockout_after_5_failures_and_reset_unlocks():
    admin()
    auth.create_user("yamada", "山田", "staff", PW, by="admin", must_change=False)
    for _ in range(auth.MAX_FAILED):
        with pytest.raises(auth.AuthError):
            auth.authenticate("yamada", "wrong-pass1")
    with pytest.raises(auth.AuthError, match="ロック"):
        auth.authenticate("yamada", PW)  # 正しいパスワードでもロック中は入れない
    tmp = auth.reset_password("yamada", by="admin")
    assert auth.authenticate("yamada", tmp)["must_change"] == 1


def test_first_admin_only_once():
    admin()
    with pytest.raises(auth.AuthError):
        auth.create_first_admin("other", "別人", PW)


@pytest.mark.parametrize("pw", ["short1", "onlyletters", "12345678"])
def test_weak_password_rejected(pw):
    with pytest.raises(auth.AuthError):
        auth.validate_password(pw)


def test_change_password_clears_must_change():
    admin()
    auth.create_user("sato", "佐藤", "viewer", PW, by="admin", must_change=True)
    auth.change_password("sato", PW, "NewPassw0rd")
    u = auth.authenticate("sato", "NewPassw0rd")
    assert u["must_change"] == 0


# ---- ユーザー管理 ------------------------------------------------------------------
def test_cannot_remove_last_admin():
    admin()
    with pytest.raises(auth.AuthError):
        auth.set_active("admin", False, by="admin")
    with pytest.raises(auth.AuthError):
        auth.set_role("admin", "staff", by="admin")
    auth.create_user("admin2", "管理 花子", "admin", PW, by="admin")
    auth.set_role("admin", "staff", by="admin2")  # 管理者が2人いれば変更できる
    assert auth.get_user("admin")["role"] == "staff"


def test_disabled_user_cannot_login():
    admin()
    auth.create_user("taisyoku", "退職者", "staff", PW, by="admin", must_change=False)
    auth.set_active("taisyoku", False, by="admin")
    with pytest.raises(auth.AuthError, match="無効"):
        auth.authenticate("taisyoku", PW)


def test_duplicate_and_invalid_username():
    admin()
    with pytest.raises(auth.AuthError):
        auth.create_user("admin", "重複", "staff", PW, by="admin")
    with pytest.raises(auth.AuthError):
        auth.create_user("日本語名", "x", "staff", PW, by="admin")


# ---- 設定（画面からの変更が config に反映される） ----------------------------------------
def test_settings_override_config_and_validate():
    assert load_config()["journal"]["debit_account_code"] == "7410"
    db.set_setting("journal.debit_account_code", settings.validate("journal.debit_account_code", "7420"), "admin")
    db.set_setting("ai.enabled", settings.validate("ai.enabled", False), "admin")
    cfg = load_config()
    assert cfg["journal"]["debit_account_code"] == "7420" and cfg["ai"]["enabled"] is False
    with pytest.raises(ValueError):
        settings.validate("journal.debit_account_code", "abc")
    with pytest.raises(ValueError):
        settings.validate("journal.encoding", "euc-jp")


# ---- 集計結果のDB保存とSQL集計 -------------------------------------------------------
@pytest.fixture
def two_months(tmp_path):
    """エラーなしの2か月分を作って実行し、DBに保存する"""
    cfg = copy.deepcopy(load_config())
    for k in cfg["paths"]:
        cfg["paths"][k] = str(tmp_path / k)
    results = {}
    for month, seed in [("2026-10", 42), ("2026-11", 7)]:
        make_dummy(Path(cfg["paths"]["input_dir"]) / month, month, seed)
        res = run(month, cfg, use_ai=False, user="tester")
        # 仕込みエラーを画面と同じ保存処理で直してから再集計
        for i in [i for i in res.issues if i.severity == "エラー"]:
            if i.code == "P001":
                save_correction(cfg, month, "pdf_overrides", i.card_no, {"amount": i.ref["subtotal"]}, "tester")
            elif i.code in ("M001", "M002", "L003"):
                save_correction(cfg, month, "card_overrides", i.card_no,
                                {"facility_code": "F001", "category": "施設", "user_name": i.user}, "tester")
            elif i.code in ("A001", "A002"):
                save_correction(cfg, month, "alloc_overrides", i.ref["employee_no"],
                                [{"facility_code": "F001", "ratio": 60}, {"facility_code": "F002", "ratio": 40}], "tester")
        results[month] = run(month, cfg, use_ai=False, user="tester")
        assert results[month].can_export
    return results


def test_monthly_results_saved_and_sql_totals_match(two_months):
    total = reports.monthly_total()
    assert list(total["対象月"]) == ["2026-10", "2026-11"]
    for _, r in total.iterrows():
        assert r["合計"] == two_months[r["対象月"]].totals["CSV借方合計"]
        assert r["直接利用"] + r["按分受入"] == r["合計"]


def test_ranking_and_lag(two_months):
    rk = reports.ranking("2026-11")
    assert rk["合計"].is_monotonic_decreasing and abs(rk["構成比"].sum() - 100) < 1
    tr = reports.facility_trend()
    f = tr[tr["施設コード"] == "F001"].sort_values("対象月")
    assert pd.isna(f["前月差"].iloc[0])
    assert f["前月差"].iloc[1] == f["金額"].iloc[1] - f["金額"].iloc[0]


def test_rerun_replaces_month_not_duplicates(two_months):
    with db.connect() as con:
        n = con.execute("SELECT COUNT(*) FROM facility_monthly WHERE month='2026-11'").fetchone()[0]
        runs = con.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    assert n == len(set(two_months["2026-11"].alloc_rows["facility_code"]))
    assert runs == 4  # 各月：エラーありの実行＋修正後の実行


def test_audit_log_records_run_and_corrections(two_months):
    acts = [r["action"] for r in db.recent_logs(username="tester")]
    assert "集計実行" in acts and "修正" in acts


def test_demo_mode_is_default_and_protects_demo_accounts(monkeypatch):
    from src import demo
    monkeypatch.delenv("GASCARD_DEMO", raising=False)
    assert demo.enabled()  # 何も設定しなければデモモード（安全側）
    demo.seed(load_config())
    with pytest.raises(auth.AuthError):
        auth.set_active("demo_admin", False, by="x")
    monkeypatch.setenv("GASCARD_DEMO", "0")
    assert not demo.enabled()


@pytest.mark.parametrize("bad", ["../../etc", "2026-13", "2026-1", "2026-10/../x", ""])
def test_month_path_traversal_rejected(bad):
    from src.pipeline import check_month, corrections_path
    with pytest.raises(ValueError):
        check_month(bad)
    with pytest.raises(ValueError):
        corrections_path(load_config(), bad)


def test_sql_injection_in_username_is_harmless():
    admin()
    with pytest.raises(auth.AuthError):
        auth.authenticate("admin' OR '1'='1", "x")
    assert sqlite3.connect(db.db_path()).execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
