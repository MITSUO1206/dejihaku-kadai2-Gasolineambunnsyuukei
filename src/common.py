"""共通処理：設定読込・文字列正規化・月の扱い"""
from __future__ import annotations

import calendar
import os
import re
import unicodedata
from datetime import date
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

# .env（APIキーなど）を読み込む。空欄の値は「未設定」として扱う
load_dotenv(ROOT / ".env")
for _k in ("ANTHROPIC_API_KEY",):
    if os.environ.get(_k, "").strip() == "":
        os.environ.pop(_k, None)


def ai_key_configured() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def load_config(path: Path | None = None, with_db: bool = True) -> dict:
    """config.yaml を読み、管理画面で変更された設定（SQLite）があれば上書きする"""
    with open(path or ROOT / "config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if with_db:
        from .settings import apply_overrides
        apply_overrides(cfg)
    return cfg


def nfkc(value) -> str:
    """全角→半角などを揃え、前後の空白を除く。None/NaN は空文字にする"""
    if value is None:
        return ""
    s = str(value)
    if s.lower() == "nan":
        return ""
    return unicodedata.normalize("NFKC", s).strip()


def normalize_card_no(value) -> str:
    """カード番号を数字だけにする（ハイフン・空白・全角を除去）"""
    return re.sub(r"\D", "", nfkc(value))


def format_card_no(card_no: str) -> str:
    """表示用：4桁ごとにハイフン"""
    return "-".join(card_no[i:i + 4] for i in range(0, len(card_no), 4))


def parse_amount(value) -> int | None:
    """'¥12,345' '１２，３４５円' などを int に。読めなければ None"""
    s = nfkc(value).replace(",", "").replace("¥", "").replace("円", "").replace(" ", "")
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    return None


def month_end(month: str) -> date:
    """'2026-10' → date(2026, 10, 31)"""
    y, m = map(int, month.split("-"))
    return date(y, m, calendar.monthrange(y, m)[1])


def month_start(month: str) -> date:
    y, m = map(int, month.split("-"))
    return date(y, m, 1)


def month_label(month: str) -> str:
    y, m = map(int, month.split("-"))
    return f"{y}年{m}月"
