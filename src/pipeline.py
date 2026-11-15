"""①〜⑤を順に実行し、整合性チェックをして成果物を出力する

担当者の修正は data/corrections/<対象月>.json に保存し、実行のたびに当て直す（元データは書き換えない）。

コマンドで実行:  python -m src.pipeline --month 2026-10 [--no-ai]
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

from .allocator import allocate, load_allocation
from .common import ROOT, load_config
from .errors import ERROR, Issue
from .exporter import build_journal, write_errors_xlsx, write_journal_csv, write_summary_xlsx
from .ledger import Ledger, load_ledger
from .matcher import match
from .pdf_reader import Statement, load_statement

EMPTY_CORRECTIONS = {"card_overrides": {}, "pdf_overrides": {}, "alloc_overrides": {}, "history": []}


# ---- 修正ファイル -----------------------------------------------------------
MONTH_RE = re.compile(r"^20\d{2}-(0[1-9]|1[0-2])$")


def check_month(month: str) -> str:
    """対象月はフォルダ名・ファイル名に使うため、YYYY-MM 以外は受け付けない"""
    if not MONTH_RE.match(month or ""):
        raise ValueError(f"対象月の形式が正しくありません: {month!r}（例: 2026-10）")
    return month


def corrections_path(cfg: dict, month: str) -> Path:
    return ROOT / cfg["paths"]["corrections_dir"] / f"{check_month(month)}.json"


def load_corrections(cfg: dict, month: str) -> dict:
    p = corrections_path(cfg, month)
    data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    return {k: data.get(k, type(v)()) for k, v in EMPTY_CORRECTIONS.items()}


def save_correction(cfg: dict, month: str, kind: str, key: str, value, who: str = "", note: str = "") -> None:
    """kind: card_overrides / pdf_overrides / alloc_overrides。value=None で取消"""
    data = load_corrections(cfg, month)
    if value is None:
        data[kind].pop(key, None)
    else:
        data[kind][key] = value
    data["history"].append({"time": datetime.now().isoformat(timespec="seconds"), "who": who,
                            "kind": kind, "key": key, "value": value, "note": note})
    p = corrections_path(cfg, month)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    from . import db
    labels = {"card_overrides": "カード情報", "pdf_overrides": "金額", "alloc_overrides": "按分"}
    db.log(who or "-", "修正取消" if value is None else "修正", f"{month} {labels.get(kind, kind)} {key} {note}".strip())


# ---- 入力ファイル -----------------------------------------------------------
def find_inputs(cfg: dict, month: str) -> dict[str, Path]:
    d = ROOT / cfg["paths"]["input_dir"] / check_month(month)
    found = {}
    for key, pattern in cfg["input_files"].items():
        hits = sorted(d.glob(pattern))
        if not hits:
            raise FileNotFoundError(f"{d} に {pattern} がありません")
        found[key] = hits[-1]  # 複数あれば名前順で最後（最新版の想定）
    return found


# ---- 実行 -------------------------------------------------------------------
@dataclass
class Result:
    month: str
    statement: Statement
    ledger: Ledger
    cards: pd.DataFrame
    alloc_rows: pd.DataFrame
    journal: pd.DataFrame
    issues: list[Issue]
    totals: dict = field(default_factory=dict)
    totals_ok: bool = False
    outputs: dict = field(default_factory=dict)

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.severity == ERROR)

    @property
    def can_export(self) -> bool:
        return self.error_count == 0 and self.totals_ok


def run(month: str, cfg: dict | None = None, use_ai: bool | None = None, ai_client=None,
        inputs: dict[str, Path] | None = None, write: bool = True, user: str | None = None) -> Result:
    """user を渡すと、実行記録と（CSVを出せた月は）施設別の確定金額を SQLite に保存する"""
    cfg = cfg or load_config()
    inputs = inputs or find_inputs(cfg, month)
    corr = load_corrections(cfg, month)

    led = load_ledger(inputs["ledger_xlsx"], cfg, month, corr["card_overrides"])            # ①
    st = load_statement(inputs["statement_pdf"], cfg, corr["pdf_overrides"], use_ai, ai_client)  # ②
    cards, m_issues = match(st, led, cfg)                                                     # ③

    # 台帳エラーがあるカード・読取エラーのカードは按分・CSVに回さない
    blocked = {i.card_no for i in led.issues + st.issues if i.severity == ERROR and i.card_no}
    cards.loc[cards["card_no"].isin(blocked), "status"] = "エラー"

    alloc = load_allocation(inputs["allocation_xlsx"], cfg, month)
    alloc_rows, a_issues = allocate(cards, alloc, led.facilities, cfg, corr["alloc_overrides"])  # ④
    journal = build_journal(alloc_rows, led.facilities, cfg, month)                           # ⑤

    # 整合性チェック：明細合計 = 按分前合計 + エラー保留分、按分前 = 按分後 = CSV
    allocated_cards = set(alloc_rows["card_no"])
    held = int(cards[~cards["card_no"].isin(allocated_cards)]["amount"].sum())
    totals = {
        "PDF請求合計": st.grand_total or 0,
        "明細読取合計": int(st.total),
        "按分前合計（処理対象カード）": int(cards[cards["card_no"].isin(allocated_cards)]["amount"].sum()),
        "按分後合計": int(alloc_rows["amount"].sum()),
        "CSV借方合計": int(journal["借方金額"].sum()) if len(journal) else 0,
        "CSV貸方合計": int(journal["貸方金額"].sum()) if len(journal) else 0,
        "エラーで保留中の金額": held,
    }
    totals_ok = (totals["PDF請求合計"] == totals["明細読取合計"]
                 == totals["按分前合計（処理対象カード）"] + held
                 and totals["按分前合計（処理対象カード）"] == totals["按分後合計"]
                 == totals["CSV借方合計"] == totals["CSV貸方合計"])
    issues = led.issues + st.issues + m_issues + a_issues
    if not totals_ok and held == 0 and not any(i.severity == ERROR for i in issues):
        issues.append(Issue("C001", json.dumps(totals, ensure_ascii=False)))

    res = Result(month, st, led, cards, alloc_rows, journal, issues, totals, totals_ok)
    if write:
        write_outputs(res, cfg)
    if user:
        from . import db
        db.save_run(res, user)
        db.log(user, "集計実行", f"{month} エラー{res.error_count}件 CSV{'出力' if res.can_export else '未出力'}")
    return res


def write_outputs(res: Result, cfg: dict) -> None:
    out = ROOT / cfg["paths"]["output_dir"] / res.month
    ym = res.month.replace("-", "")
    paths = {"summary": out / f"カード別施設別集計_{ym}.xlsx", "errors": out / f"エラー一覧_{ym}.xlsx",
             "csv": out / f"費用振替_{ym}.csv"}
    write_summary_xlsx(res, paths["summary"])
    write_errors_xlsx(res, paths["errors"])
    if res.can_export:
        write_journal_csv(res.journal, paths["csv"], cfg)
    else:
        paths["csv"].unlink(missing_ok=True)  # 古いCSVが残って誤って取り込まれるのを防ぐ
        paths.pop("csv")
    res.outputs = paths


def main() -> None:
    ap = argparse.ArgumentParser(description="ガソリンカード 集計・按分・費用振替CSV作成")
    ap.add_argument("--month", required=True, help="対象月 例: 2026-10")
    ap.add_argument("--no-ai", action="store_true", help="AI読取補助を使わない")
    args = ap.parse_args()
    res = run(args.month, use_ai=False if args.no_ai else None, user="(コマンド)")
    print(f"対象月 {args.month}: エラー {res.error_count} 件 / 全指摘 {len(res.issues)} 件")
    for k, v in res.totals.items():
        print(f"  {k:<20} {v:>12,}")
    print("  整合性: " + ("OK" if res.totals_ok else "NG"))
    for k, p in res.outputs.items():
        print(f"  出力 {k}: {p}")
    if not res.can_export:
        print("  ※ エラーが残っているため費用振替CSVは出力していません")


if __name__ == "__main__":
    main()
