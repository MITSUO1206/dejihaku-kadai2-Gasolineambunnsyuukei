"""① ガソリンカード管理台帳の取込

台帳には変更履歴（有効開始日・有効終了日）を持たせ、対象月に有効な行を選ぶ。
これにより、利用者・施設が変わっても最新の台帳を置き直すだけで対応できる。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from .common import month_end, month_start, nfkc, normalize_card_no
from .errors import Issue

VALID_CATEGORIES = {"施設", "AM", "アドバイザー"}


@dataclass
class Ledger:
    cards: pd.DataFrame            # 対象月に有効なカード（card_no で一意）
    all_rows: pd.DataFrame         # 全履歴（期間外判定に使う）
    facilities: pd.DataFrame       # 施設マスタ（facility_code で一意）
    issues: list[Issue] = field(default_factory=list)

    def facility_name(self, code: str) -> str:
        hit = self.facilities[self.facilities["facility_code"] == code]
        return hit.iloc[0]["facility_name"] if len(hit) else ""


def _to_date(v):
    if v is None or (isinstance(v, float) and pd.isna(v)) or nfkc(v) == "":
        return None
    return pd.to_datetime(v).date()


def load_ledger(path: Path, cfg: dict, month: str, card_overrides: dict | None = None) -> Ledger:
    lc = cfg["ledger"]
    raw = pd.read_excel(path, sheet_name=lc["sheet"], dtype=str)
    fac_raw = pd.read_excel(path, sheet_name=lc["facility_sheet"], dtype=str)

    rename = {v: k for k, v in lc["columns"].items()}
    missing_cols = [v for v in lc["columns"].values() if v not in raw.columns]
    if missing_cols:
        raise ValueError(f"管理台帳に列がありません: {missing_cols}（config.yaml の ledger.columns を確認）")
    df = raw.rename(columns=rename)[list(lc["columns"].keys())].copy()
    df["excel_row"] = raw.index + 2  # Excel上の行番号（見出しが1行目）
    for c in ["facility_code", "facility_name", "category", "employee_no", "user_name", "card_holder"]:
        df[c] = df[c].map(nfkc)
    df["card_no"] = df["card_no"].map(normalize_card_no)
    df["valid_from"] = df["valid_from"].map(_to_date)
    df["valid_to"] = df["valid_to"].map(_to_date)
    df["source"] = "台帳"

    fac = fac_raw.rename(columns={v: k for k, v in lc["facility_columns"].items()})
    fac = fac[list(lc["facility_columns"].keys())].copy()
    for c in fac.columns:
        fac[c] = fac[c].map(nfkc)

    # 画面からの補正（台帳にないカードの臨時登録など）を追加・上書き
    if card_overrides:
        for card_no, ov in card_overrides.items():
            df = df[df["card_no"] != card_no]
            row = {"card_no": card_no, "facility_code": ov.get("facility_code", ""),
                   "facility_name": "", "category": ov.get("category", "施設"),
                   "employee_no": ov.get("employee_no", ""), "user_name": ov.get("user_name", ""),
                   "card_holder": ov.get("card_holder", ""), "valid_from": None, "valid_to": None,
                   "excel_row": None, "source": "画面補正"}
            df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)

    fac_codes = set(fac["facility_code"])
    name_map = dict(zip(fac["facility_code"], fac["facility_name"]))
    df["facility_name"] = df["facility_code"].map(name_map).fillna(df["facility_name"])

    issues: list[Issue] = []
    m_start, m_end = month_start(month), month_end(month)
    active_mask = df.apply(
        lambda r: (r["valid_from"] is None or r["valid_from"] <= m_end)
        and (r["valid_to"] is None or r["valid_to"] >= m_start), axis=1)
    active = df[active_mask].copy()

    for _, r in active.iterrows():
        where = f"台帳{int(r['excel_row'])}行目" if pd.notna(r["excel_row"]) else "画面補正"
        blanks = [label for key, label in [("card_no", "カード番号"), ("facility_code", "施設コード"),
                                           ("category", "区分")] if not r[key]]
        if blanks:
            issues.append(Issue("L001", f"{where}: {'・'.join(blanks)} が空欄", card_no=r["card_no"],
                                user=r["user_name"]))
        if r["facility_code"] and r["facility_code"] not in fac_codes:
            issues.append(Issue("L003", f"{where}: 施設コード {r['facility_code']} は施設一覧にありません",
                                card_no=r["card_no"], facility=r["facility_code"], user=r["user_name"]))
        if r["category"] and r["category"] not in VALID_CATEGORIES:
            issues.append(Issue("L004", f"{where}: 区分「{r['category']}」", card_no=r["card_no"]))

    dup = active[active.duplicated("card_no", keep=False) & (active["card_no"] != "")]
    for card_no, g in dup.groupby("card_no"):
        rows = ", ".join(str(int(x)) for x in g["excel_row"].dropna())
        issues.append(Issue("L002", f"台帳{rows}行目が同じ期間に重複", card_no=card_no))
    active = active.drop_duplicates("card_no", keep="last")

    return Ledger(cards=active.reset_index(drop=True), all_rows=df, facilities=fac, issues=issues)
