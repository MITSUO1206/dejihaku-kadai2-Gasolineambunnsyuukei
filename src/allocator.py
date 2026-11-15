"""④ AM・アドバイザー等の費用按分

端数処理は最大剰余法：
  1. 金額×割合 を切り捨てて各施設に配る
  2. 配り残し（元の金額との差）を、切り捨てた端数が大きい施設から1円ずつ配る
  → 按分後の合計が元の金額と必ず一致する
"""
from __future__ import annotations

from decimal import ROUND_FLOOR, Decimal
from pathlib import Path

import pandas as pd

from .common import nfkc
from .errors import Issue


def split_amount(amount: int, ratios: list[float]) -> list[int]:
    """amount を ratios（合計100）で按分。合計は必ず amount に一致"""
    if not ratios:
        return []
    total = sum(Decimal(str(r)) for r in ratios)
    exact = [Decimal(amount) * Decimal(str(r)) / total for r in ratios]
    base = [int(x.to_integral_value(rounding=ROUND_FLOOR)) for x in exact]
    rest = amount - sum(base)
    # 端数の大きい順（同じなら先の行）に1円ずつ
    order = sorted(range(len(ratios)), key=lambda i: (-(exact[i] - base[i]), i))
    for i in order[:rest]:
        base[i] += 1
    return base


def load_allocation(path: Path, cfg: dict, month: str) -> pd.DataFrame:
    ac = cfg["allocation"]
    raw = pd.read_excel(path, sheet_name=ac["sheet"], dtype=str)
    df = raw.rename(columns={v: k for k, v in ac["columns"].items()})[list(ac["columns"].keys())].copy()
    for c in ["month", "employee_no", "employee_name", "facility_code"]:
        df[c] = df[c].map(nfkc)
    df["ratio"] = pd.to_numeric(df["ratio"].map(nfkc), errors="coerce")
    df["month"] = df["month"].str[:7]
    months_in_file = sorted(m for m in df["month"].unique() if m)
    df = df[(df["month"] == month) | (df["month"] == "")].reset_index(drop=True)
    df.attrs["months_in_file"] = months_in_file  # 対象月の行が無いときの案内に使う
    return df


def allocate(cards: pd.DataFrame, alloc: pd.DataFrame, facilities: pd.DataFrame, cfg: dict,
             alloc_overrides: dict | None = None) -> tuple[pd.DataFrame, list[Issue]]:
    """カード別集計 → 施設別の振替明細（1カード×1按分先 = 1行）"""
    alloc_cats = set(cfg["ledger"]["allocation_categories"])
    fac_codes = set(facilities["facility_code"])
    fac_name = dict(zip(facilities["facility_code"], facilities["facility_name"]))
    overrides = alloc_overrides or {}
    out, issues = [], []

    for _, c in cards[cards["status"] == "OK"].iterrows():
        base = {"card_no": c["card_no"], "category": c["category"], "user_name": c["user_name"],
                "employee_no": c["employee_no"], "card_amount": c["amount"]}
        if c["category"] not in alloc_cats:
            out.append({**base, "facility_code": c["facility_code"], "facility_name": c["facility_name"],
                        "ratio": 100.0, "amount": c["amount"], "method": "直接"})
            continue

        emp = c["employee_no"]
        if emp in overrides:
            plan = pd.DataFrame(overrides[emp])
            src = "画面補正"
        else:
            plan = alloc[alloc["employee_no"] == emp]
            src = "按分資料"
        if plan.empty:
            detail = f"社員番号 {emp} が給与按分資料にありません"
            if alloc.empty and alloc.attrs.get("months_in_file"):
                detail += ("（給与按分資料に対象月の行がありません。資料にある月: "
                           + "・".join(alloc.attrs["months_in_file"]) + "。資料の「対象月」を確認してください）")
            issues.append(Issue("A001", detail, card_no=c["card_no"],
                                user=c["user_name"], amount=c["amount"], ref={"employee_no": emp}))
            continue
        bad = [fc for fc in plan["facility_code"] if fc not in fac_codes]
        if bad:
            issues.append(Issue("A003", f"按分先 {', '.join(bad)}", card_no=c["card_no"], user=c["user_name"],
                                amount=c["amount"], ref={"employee_no": emp}))
            continue
        ratio_sum = round(float(plan["ratio"].sum()), 4)
        if plan["ratio"].isna().any() or abs(ratio_sum - 100) > 1e-6:
            issues.append(Issue("A002", f"按分割合の合計 {ratio_sum}%", card_no=c["card_no"],
                                user=c["user_name"], amount=c["amount"],
                                ref={"employee_no": emp, "plan": plan[["facility_code", "ratio"]]
                                     .to_dict("records")}))
            continue
        amounts = split_amount(int(c["amount"]), plan["ratio"].tolist())
        for (_, p), amt in zip(plan.iterrows(), amounts):
            out.append({**base, "facility_code": p["facility_code"], "facility_name": fac_name[p["facility_code"]],
                        "ratio": float(p["ratio"]), "amount": amt, "method": f"按分（{src}）"})

    cols = ["card_no", "category", "user_name", "employee_no", "card_amount", "facility_code", "facility_name",
            "ratio", "amount", "method"]
    return pd.DataFrame(out, columns=cols), issues
