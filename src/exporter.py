"""⑤ 費用振替CSV・集計一覧Excel・エラー一覧Excel の作成"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from .common import format_card_no, month_end, month_label


def build_journal(alloc_rows: pd.DataFrame, facilities: pd.DataFrame, cfg: dict, month: str) -> pd.DataFrame:
    jc = cfg["journal"]
    dept = dict(zip(facilities["facility_code"], facilities["dept_code"]))
    by_fac = (alloc_rows.groupby(["facility_code", "facility_name"], as_index=False)["amount"].sum()
              .query("amount != 0").sort_values("facility_code"))
    d = month_end(month).strftime("%Y/%m/%d")
    rows = []
    for i, r in enumerate(by_fac.itertuples(), start=1):
        rows.append({
            "伝票日付": d, "伝票番号": f"GS{month.replace('-', '')}", "行番号": i,
            "借方勘定科目コード": jc["debit_account_code"], "借方勘定科目名": jc["debit_account_name"],
            "借方部門コード": dept.get(r.facility_code, ""), "借方金額": int(r.amount),
            "貸方勘定科目コード": jc["credit_account_code"], "貸方勘定科目名": jc["credit_account_name"],
            "貸方部門コード": jc["credit_dept_code"], "貸方金額": int(r.amount),
            "摘要": jc["summary_format"].format(month=month_label(month), facility_name=r.facility_name),
        })
    return pd.DataFrame(rows, columns=jc["columns"])


def write_journal_csv(journal: pd.DataFrame, path: Path, cfg: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    journal.to_csv(path, index=False, encoding=cfg["journal"]["encoding"], lineterminator="\r\n")


def _autofit(ws) -> None:
    for col in ws.columns:
        width = max(len(str(c.value or "")) for c in col)
        ws.column_dimensions[get_column_letter(col[0].column)].width = min(max(8, width * 1.6), 60)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="305496")
    ws.freeze_panes = "A2"


def _write_sheets(path: Path, sheets: dict[str, pd.DataFrame]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        for name, df in sheets.items():
            df.to_excel(w, sheet_name=name, index=False)
            _autofit(w.sheets[name])
            if "金額" in "".join(map(str, df.columns)):
                for col in w.sheets[name].iter_cols(min_row=2):
                    if "金額" in str(w.sheets[name].cell(1, col[0].column).value) or \
                            str(w.sheets[name].cell(1, col[0].column).value) in ("小計", "合計"):
                        for c in col:
                            c.number_format = "#,##0"


def summary_sheets(res) -> dict[str, pd.DataFrame]:
    cards = res.cards.copy()
    al = res.alloc_rows.copy()
    # カード別一覧（按分先ごとに1行）
    card_view = cards.merge(al[["card_no", "facility_code", "facility_name", "ratio", "amount", "method"]]
                            .rename(columns={"facility_code": "按分先施設コード", "facility_name": "按分先施設名",
                                             "ratio": "按分割合(%)", "amount": "按分金額", "method": "計上方法"}),
                            on="card_no", how="left")
    card_view["card_no"] = card_view["card_no"].map(format_card_no)
    card_view = card_view.rename(columns={
        "card_no": "カード番号", "holder_pdf": "カード名義(PDF)", "facility_code": "台帳施設コード",
        "facility_name": "台帳施設名", "category": "区分", "employee_no": "社員番号", "user_name": "利用者",
        "amount": "利用金額", "lines": "明細行数", "ai_lines": "AI読取行数", "status": "照合結果"})
    card_view = card_view[["カード番号", "カード名義(PDF)", "区分", "台帳施設コード", "台帳施設名", "社員番号", "利用者",
                           "利用金額", "明細行数", "AI読取行数", "照合結果", "按分先施設コード", "按分先施設名",
                           "按分割合(%)", "按分金額", "計上方法"]]

    fac = res.ledger.facilities.copy()
    direct = al[al["method"] == "直接"].groupby("facility_code")["amount"].sum()
    shared = al[al["method"] != "直接"].groupby("facility_code")["amount"].sum()
    fac["直接利用金額"] = fac["facility_code"].map(direct).fillna(0).astype(int)
    fac["按分受入金額"] = fac["facility_code"].map(shared).fillna(0).astype(int)
    fac["合計金額"] = fac["直接利用金額"] + fac["按分受入金額"]
    fac = fac.rename(columns={"facility_code": "施設コード", "facility_name": "施設名", "dept_code": "部門コード"})
    total_row = pd.DataFrame([{"施設コード": "合計", "直接利用金額": fac["直接利用金額"].sum(),
                               "按分受入金額": fac["按分受入金額"].sum(), "合計金額": fac["合計金額"].sum()}])
    fac = pd.concat([fac, total_row], ignore_index=True)

    lines = [{"カード番号": format_card_no(c.card_no), "名義": c.holder, "ページ": c.page, "利用日": ln["date"],
              "給油所": ln["station"], "品目": ln["item"], "数量(L)": ln["liters"], "金額": ln["amount"],
              "読取方法": ln["source"]} for c in res.statement.cards.values() for ln in c.lines]

    check = pd.DataFrame([{"項目": k, "金額": v} for k, v in res.totals.items()]
                         + [{"項目": "判定", "金額": "一致" if res.totals_ok else "不一致"}])
    return {"整合性チェック": check, "カード別一覧": card_view, "施設別集計": fac, "利用明細": pd.DataFrame(lines)}


def write_summary_xlsx(res, path: Path) -> None:
    _write_sheets(path, summary_sheets(res))


def write_errors_xlsx(res, path: Path) -> None:
    order = {"エラー": 0, "要確認": 1, "情報": 2}
    df = pd.DataFrame([i.to_row() for i in res.issues])
    if df.empty:
        df = pd.DataFrame(columns=["重要度", "ステップ", "コード", "内容", "詳細", "カード番号", "施設", "利用者", "金額", "対処方法"])
    else:
        df["カード番号"] = df["カード番号"].map(lambda x: format_card_no(x) if x else "")
        df = df.sort_values(by=["重要度", "コード"], key=lambda s: s.map(order) if s.name == "重要度" else s)
    _write_sheets(path, {"エラー一覧": df})
