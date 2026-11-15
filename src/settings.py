"""画面から変えられる設定

config.yaml の中で、業務の都合で変わりうる項目だけをここに並べる。
画面で変更した値は SQLite の settings テーブルに保存され、load_config() のたびに上書きされる。
"""
from __future__ import annotations

import re

from . import db

# key は config.yaml 内の位置（"journal.debit_account_code" = cfg["journal"]["debit_account_code"]）
DEFS = [
    {"key": "journal.debit_account_code", "label": "借方 勘定科目コード", "kind": "text", "pattern": r"^\d{1,10}$",
     "help": "施設側に計上する科目のコード（数字）"},
    {"key": "journal.debit_account_name", "label": "借方 勘定科目名", "kind": "text", "pattern": r"^.{1,30}$"},
    {"key": "journal.credit_account_code", "label": "貸方 勘定科目コード", "kind": "text", "pattern": r"^\d{1,10}$",
     "help": "本部側（付け替え元）の科目のコード（数字）"},
    {"key": "journal.credit_account_name", "label": "貸方 勘定科目名", "kind": "text", "pattern": r"^.{1,30}$"},
    {"key": "journal.credit_dept_code", "label": "本部の部門コード", "kind": "text", "pattern": r"^[0-9A-Za-z]{1,10}$"},
    {"key": "journal.encoding", "label": "CSVの文字コード", "kind": "select",
     "options": {"cp932": "Shift_JIS（多くの会計ソフト）", "utf-8-sig": "UTF-8（BOM付き）"}},
    {"key": "ai.enabled", "label": "AI読取補助を使う（既定）", "kind": "bool",
     "help": "オフにすると、読めない行はAIに送らず手入力に回します"},
]
BY_KEY = {d["key"]: d for d in DEFS}


def _get(cfg: dict, key: str):
    cur = cfg
    for k in key.split("."):
        cur = cur[k]
    return cur


def _set(cfg: dict, key: str, value) -> None:
    *path, last = key.split(".")
    cur = cfg
    for k in path:
        cur = cur[k]
    cur[last] = value


def to_value(key: str, raw: str):
    return raw == "1" if BY_KEY[key]["kind"] == "bool" else raw


def apply_overrides(cfg: dict) -> dict:
    for key, raw in db.get_settings().items():
        if key in BY_KEY:
            _set(cfg, key, to_value(key, raw))
    return cfg


def validate(key: str, value) -> str:
    """画面の入力値を検証して、保存用の文字列にする"""
    d = BY_KEY[key]
    if d["kind"] == "bool":
        return "1" if value else "0"
    s = str(value).strip()
    if d["kind"] == "select" and s not in d["options"]:
        raise ValueError(f"{d['label']} の値が正しくありません")
    if d.get("pattern") and not re.match(d["pattern"], s):
        raise ValueError(f"{d['label']} の形式が正しくありません")
    return s


def current(cfg: dict, key: str):
    return _get(cfg, key)
