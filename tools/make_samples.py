"""「入力ファイル見本」フォルダを作る

  入力ファイル見本/
    ダミーデータ_2026-10/   … そのまま画面からアップロードして試せる架空データ（エラー入り）
    ダミーデータ_2026-11/   … 翌月分（エラーなし版。カード変更・按分の確認用）
    テンプレート/           … 実データを入れるための空の様式（列名は config.yaml と一致）

使い方:  python tools/make_samples.py
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.common import ROOT  # noqa: E402
from tools.make_dummy import build_data, write_excels, write_pdf  # noqa: E402

OUT = ROOT / "入力ファイル見本"


def style(path: Path, notes: dict[str, list[str]] | None = None) -> None:
    wb = load_workbook(path)
    for ws in wb.worksheets:
        for c in ws[1]:
            c.font = Font(bold=True, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="305496")
        for col in ws.columns:
            w = max(len(str(c.value or "")) for c in col)
            ws.column_dimensions[get_column_letter(col[0].column)].width = min(max(10, w * 1.8), 40)
        ws.freeze_panes = "A2"
    for sheet, lines in (notes or {}).items():
        ws = wb.create_sheet(sheet)
        for i, line in enumerate(lines, start=1):
            ws.cell(i, 1, line)
        ws.column_dimensions["A"].width = 110
    wb.save(path)


def clean_version(data: dict) -> dict:
    """エラーを仕込まない版（翌月用）"""
    a = data["answer"]
    data["cards"] = [c for c in data["cards"] if not c["holder"].startswith("ミトウロク")]
    for c in data["cards"]:
        for ln in c["lines"]:
            ln.pop("garbled", None)
    a["grand_total"] = sum(c["subtotal"] for c in data["cards"])
    return data


def make_dummy_set(month: str, seed: int, clean: bool) -> Path:
    d = OUT / f"ダミーデータ_{month}"
    d.mkdir(parents=True, exist_ok=True)
    data = build_data(month, seed)
    if clean:
        data = clean_version(data)
        # 台帳側の仕込みエラーも戻す
        for r in data["ledger"]:
            if r["施設コード"] == "F099":
                r["施設コード"] = "F001"
            if r["カード名義"] == "キュウメイギ タロウ":
                r["カード名義"] = next(c["holder"] for c in data["cards"] if c["card_no"] == r["カード番号"])
            r["有効終了日"] = r["有効終了日"] if r["備考"] == "旧所属" else None
        emps = {x["社員番号"] for x in data["allocation"]}
        for e in data["employees"]:
            if e["社員番号"] not in emps:
                data["allocation"].append({"対象月": month, "社員番号": e["社員番号"], "氏名": e["氏名"],
                                           "按分先施設コード": "F001", "按分割合": 100})
        fixed = []
        for emp, g in pd.DataFrame(data["allocation"]).groupby("社員番号", sort=False):
            g = g.copy()
            g.iloc[-1, g.columns.get_loc("按分割合")] += 100 - g["按分割合"].sum()
            fixed += g.to_dict("records")
        data["allocation"] = fixed
    ym = month.replace("-", "")
    write_pdf(data, month, d / f"利用明細_{ym}.pdf")
    write_excels(data, month, d / f"管理台帳_{ym}.xlsx", d / f"給与按分_{ym}.xlsx")
    style(d / f"管理台帳_{ym}.xlsx")
    style(d / f"給与按分_{ym}.xlsx")
    return d


def make_templates() -> Path:
    d = OUT / "テンプレート"
    d.mkdir(parents=True, exist_ok=True)
    ledger = pd.DataFrame([{
        "カード番号": "4980-0000-0000-0001", "施設コード": "F001", "施設名": "（施設名）", "区分": "施設",
        "社員番号": "", "利用者": "公用車1", "カード名義": "（カードに印字された名義）",
        "有効開始日": "2024/04/01", "有効終了日": "", "備考": "← この行は記入例。消して使ってください"}])
    fac = pd.DataFrame([{"施設コード": "F001", "施設名": "（施設名）", "部門コード": "101"}])
    p = d / "管理台帳_テンプレート.xlsx"
    with pd.ExcelWriter(p, engine="openpyxl") as w:
        ledger.to_excel(w, sheet_name="カード台帳", index=False)
        fac.to_excel(w, sheet_name="施設マスタ", index=False)
    style(p, {"記入ルール": [
        "【カード台帳】1カード1行。利用者・施設が変わったら、旧行の「有効終了日」に変更前日を入れ、新しい行を追加する",
        "区分: 施設 / AM / アドバイザー のいずれか。AM・アドバイザーは社員番号が必須（給与按分資料と照合）",
        "カード番号はハイフンあり・なし、全角どちらでも可",
        "有効開始日・有効終了日: 空欄は期限なし。日付は 2026/10/01 の形式",
        "【施設マスタ】施設コード・施設名・部門コード（部門コードは会計システムの部門コード。CSVの借方部門になる）",
        "ファイル名は「管理台帳_」で始めてください（例: 管理台帳_202611.xlsx）",
    ]})

    alloc = pd.DataFrame([
        {"対象月": "2026-11", "社員番号": "E1001", "氏名": "（氏名）", "按分先施設コード": "F001", "按分割合": 60},
        {"対象月": "2026-11", "社員番号": "E1001", "氏名": "（氏名）", "按分先施設コード": "F002", "按分割合": 40},
    ])
    p = d / "給与按分_テンプレート.xlsx"
    with pd.ExcelWriter(p, engine="openpyxl") as w:
        alloc.to_excel(w, sheet_name="按分", index=False)
    style(p, {"記入ルール": [
        "1人につき按分先の数だけ行を作る。按分割合は % で、1人分の合計を100にする（小数可: 33.33 など）",
        "対象月: YYYY-MM 形式。空欄にすると毎月共通の割合として使う",
        "按分先施設コードは管理台帳の施設マスタにあるコードを使う",
        "ファイル名は「給与按分_」で始めてください（例: 給与按分_202611.xlsx）",
    ]})
    (d / "利用明細PDFについて.txt").write_text(
        "利用明細PDFはトヨタファイナンスから届いたものをそのまま使います（テンプレートはありません）。\n"
        "ファイル名は「利用明細_」で始めてください（例: 利用明細_202611.pdf）。\n"
        "現在の読取ルールは架空の様式用です。実物のPDFに合わせて src/pdf_reader.py の PATTERNS を調整します。\n",
        encoding="utf-8")
    return d


if __name__ == "__main__":
    if OUT.exists():
        shutil.rmtree(OUT)
    print(make_dummy_set("2026-10", 42, clean=False))
    print(make_dummy_set("2026-11", 7, clean=True))
    print(make_templates())
