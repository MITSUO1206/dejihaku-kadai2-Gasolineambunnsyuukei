"""SQLite データベース（data/app.db）

テーブル:
  users            … ログインできる人（パスワードは bcrypt で暗号化して保存）
  settings         … 画面から変えられる設定（config.yaml の値を上書き）
  audit_log        … 操作ログ（ログイン・実行・修正・CSV出力・ユーザー変更・設定変更）
  runs             … 集計を実行した記録（月・合計・エラー件数）
  facility_monthly … CSVを出力できた月の、施設別の確定金額（月次推移の集計に使う）
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .common import ROOT

# 環境変数 GASCARD_DB で場所を変えられる（画面テスト用）
_DB_PATH: Path = Path(os.environ.get("GASCARD_DB") or ROOT / "data" / "app.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    username       TEXT    NOT NULL UNIQUE,
    display_name   TEXT    NOT NULL,
    role           TEXT    NOT NULL CHECK (role IN ('admin', 'staff', 'viewer')),
    password_hash  TEXT    NOT NULL,
    active         INTEGER NOT NULL DEFAULT 1,
    must_change    INTEGER NOT NULL DEFAULT 0,   -- 1 = 次回ログイン時にパスワード変更を求める
    failed_count   INTEGER NOT NULL DEFAULT 0,
    locked_until   TEXT,
    last_login_at  TEXT,
    created_at     TEXT    NOT NULL,
    updated_at     TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_by  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    username  TEXT NOT NULL,
    action    TEXT NOT NULL,
    detail    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log (ts);
CREATE TABLE IF NOT EXISTS runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    month        TEXT    NOT NULL,
    run_at       TEXT    NOT NULL,
    username     TEXT    NOT NULL,
    card_count   INTEGER NOT NULL,
    pdf_total    INTEGER NOT NULL,
    csv_total    INTEGER NOT NULL,
    error_count  INTEGER NOT NULL,
    exported     INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS facility_monthly (
    month          TEXT    NOT NULL,
    facility_code  TEXT    NOT NULL,
    facility_name  TEXT    NOT NULL,
    direct_amount  INTEGER NOT NULL,   -- 施設の車の利用分
    shared_amount  INTEGER NOT NULL,   -- AM等から按分で受け入れた分
    total_amount   INTEGER NOT NULL,
    PRIMARY KEY (month, facility_code)
);
"""


def set_path(path: Path) -> None:
    """テスト用：DBの場所を切り替える"""
    global _DB_PATH
    _DB_PATH = Path(path)


def db_path() -> Path:
    return _DB_PATH


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@contextmanager
def connect():
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(_DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        con.executescript(SCHEMA)
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


# ---- 操作ログ ------------------------------------------------------------------
def log(username: str, action: str, detail: str = "") -> None:
    with connect() as con:
        con.execute("INSERT INTO audit_log (ts, username, action, detail) VALUES (?, ?, ?, ?)",
                    (now(), username, action, detail))


def recent_logs(limit: int = 500, username: str | None = None, action: str | None = None) -> list[dict]:
    sql = "SELECT ts, username, action, detail FROM audit_log WHERE 1=1"
    params: list = []
    if username:
        sql += " AND username = ?"
        params.append(username)
    if action:
        sql += " AND action = ?"
        params.append(action)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with connect() as con:
        return [dict(r) for r in con.execute(sql, params)]


# ---- 設定 ----------------------------------------------------------------------
def get_settings() -> dict[str, str]:
    if not _DB_PATH.exists():
        return {}
    with connect() as con:
        return {r["key"]: r["value"] for r in con.execute("SELECT key, value FROM settings")}


def set_setting(key: str, value: str, by: str) -> None:
    with connect() as con:
        con.execute("""INSERT INTO settings (key, value, updated_by, updated_at) VALUES (?, ?, ?, ?)
                       ON CONFLICT(key) DO UPDATE SET value = excluded.value,
                         updated_by = excluded.updated_by, updated_at = excluded.updated_at""",
                    (key, value, by, now()))


def delete_setting(key: str) -> None:
    with connect() as con:
        con.execute("DELETE FROM settings WHERE key = ?", (key,))


# ---- 集計結果の保存 ------------------------------------------------------------
def save_run(res, username: str) -> None:
    """実行記録を残し、CSVを出力できた月は施設別の確定金額を保存し直す"""
    with connect() as con:
        con.execute("""INSERT INTO runs (month, run_at, username, card_count, pdf_total, csv_total,
                                         error_count, exported) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (res.month, now(), username, len(res.cards), int(res.totals["PDF請求合計"]),
                     int(res.totals["CSV借方合計"]), res.error_count, int(res.can_export)))
        if not res.can_export:
            return
        al = res.alloc_rows
        names = dict(zip(res.ledger.facilities["facility_code"], res.ledger.facilities["facility_name"]))
        direct = al[al["method"] == "直接"].groupby("facility_code")["amount"].sum()
        shared = al[al["method"] != "直接"].groupby("facility_code")["amount"].sum()
        codes = sorted(set(direct.index) | set(shared.index))
        con.execute("DELETE FROM facility_monthly WHERE month = ?", (res.month,))
        con.executemany(
            """INSERT INTO facility_monthly (month, facility_code, facility_name, direct_amount,
                                             shared_amount, total_amount) VALUES (?, ?, ?, ?, ?, ?)""",
            [(res.month, c, names.get(c, c), int(direct.get(c, 0)), int(shared.get(c, 0)),
              int(direct.get(c, 0) + shared.get(c, 0))) for c in codes])
