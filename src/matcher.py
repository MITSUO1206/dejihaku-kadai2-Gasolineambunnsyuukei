"""③ カード番号による照合・集計"""
from __future__ import annotations

import pandas as pd

from .errors import Issue
from .ledger import Ledger
from .pdf_reader import Statement


def _same_holder(a: str, b: str) -> bool:
    return a.replace(" ", "").replace("　", "") == b.replace(" ", "").replace("　", "")


def match(st: Statement, led: Ledger, cfg: dict) -> tuple[pd.DataFrame, list[Issue]]:
    alloc_cats = set(cfg["ledger"]["allocation_categories"])
    active = led.cards.set_index("card_no")
    known = set(led.all_rows["card_no"])
    rows, issues = [], []

    for c in st.cards.values():
        row = {"card_no": c.card_no, "holder_pdf": c.holder, "amount": c.amount,
               "lines": len(c.lines), "ai_lines": sum(1 for ln in c.lines if ln["source"] == "AI"),
               "facility_code": "", "facility_name": "", "category": "", "employee_no": "",
               "user_name": "", "ledger_source": "", "status": "OK"}
        if c.card_no in active.index:
            r = active.loc[c.card_no]
            row.update(facility_code=r["facility_code"], facility_name=r["facility_name"],
                       category=r["category"], employee_no=r["employee_no"], user_name=r["user_name"],
                       ledger_source=r["source"])
            if r["card_holder"] and not _same_holder(r["card_holder"], c.holder):
                issues.append(Issue("M003", f"PDF「{c.holder}」/ 台帳「{r['card_holder']}」",
                                    card_no=c.card_no, facility=r["facility_name"], user=r["user_name"],
                                    amount=c.amount))
            if r["category"] in alloc_cats and not r["employee_no"]:
                issues.append(Issue("M004", "社員番号が空欄", card_no=c.card_no, user=r["user_name"],
                                    amount=c.amount))
                row["status"] = "エラー"
        elif c.card_no in known:
            hist = led.all_rows[led.all_rows["card_no"] == c.card_no].iloc[-1]
            issues.append(Issue("M002", f"有効期間 {hist['valid_from']}〜{hist['valid_to']}",
                                card_no=c.card_no, facility=hist["facility_name"], user=hist["user_name"],
                                amount=c.amount, ref={"holder": c.holder}))
            row["status"] = "エラー"
        else:
            issues.append(Issue("M001", f"PDF名義「{c.holder}」", card_no=c.card_no, user=c.holder,
                                amount=c.amount, ref={"holder": c.holder}))
            row["status"] = "エラー"
        rows.append(row)

    for _, r in led.cards.iterrows():
        if r["card_no"] not in st.cards:
            issues.append(Issue("M005", "当月の利用なし", card_no=r["card_no"], facility=r["facility_name"],
                                user=r["user_name"]))

    return pd.DataFrame(rows), issues
