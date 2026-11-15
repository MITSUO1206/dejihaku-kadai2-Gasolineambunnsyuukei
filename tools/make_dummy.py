"""ダミーデータ作成ツール（実データがない段階の開発・テスト用。すべて架空）

作るもの（data/input/<対象月>/）:
  - 利用明細_YYYYMM.pdf   … トヨタファイナンス明細を想定した架空の様式
  - 管理台帳_YYYYMM.xlsx  … カード台帳＋施設マスタ
  - 給与按分_YYYYMM.xlsx  … AM・アドバイザーの施設別按分割合

わざと混ぜるエラー（テストで検出できるか確認する）:
  M001 台帳にないカード×2 / L003 施設マスタにない施設コード×1 / A001 按分資料にいないAM×1
  A002 按分割合の合計90%×1 / P001 読取不能な行×1 / M003 名義不一致×1 / M002 有効期間外×1
  M005 利用なしカード×5

使い方:  python tools/make_dummy.py --month 2026-10
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.common import ROOT, format_card_no, month_end, month_start  # noqa: E402

FACILITIES = [
    ("あおば苑", "アオバエン"), ("さくら館", "サクラカン"), ("みどりの家", "ミドリノイエ"), ("ひかり荘", "ヒカリソウ"),
    ("つばさ園", "ツバサエン"), ("ことぶき苑", "コトブキエン"), ("はなみずき", "ハナミズキ"), ("やまびこ館", "ヤマビコカン"),
    ("せせらぎ", "セセラギ"), ("あさひ苑", "アサヒエン"), ("なごみの里", "ナゴミノサト"), ("かえで館", "カエデカン"),
    ("すみれ荘", "スミレソウ"), ("ほほえみ", "ホホエミ"), ("わかば園", "ワカバエン"), ("ひだまり", "ヒダマリ"),
    ("こもれび", "コモレビ"), ("いずみ苑", "イズミエン"), ("あじさい館", "アジサイカン"), ("ゆうなぎ", "ユウナギ"),
    ("やすらぎ", "ヤスラギ"), ("もみじ荘", "モミジソウ"), ("たんぽぽ", "タンポポ"), ("しおさい", "シオサイ"),
    ("きずな館", "キズナカン"), ("ふれあい苑", "フレアイエン"), ("のぞみ", "ノゾミ"), ("あかね荘", "アカネソウ"),
    ("みなと館", "ミナトカン"), ("そよかぜ", "ソヨカゼ"),
]
SEI = ["佐藤", "鈴木", "高橋", "田中", "伊藤", "渡辺", "山本", "中村", "小林", "加藤", "吉田", "山田", "松本", "井上",
       "木村", "林", "清水", "山崎", "森", "池田", "橋本", "石川", "前田", "藤田", "岡田", "後藤", "長谷川", "村上"]
SEI_KANA = ["サトウ", "スズキ", "タカハシ", "タナカ", "イトウ", "ワタナベ", "ヤマモト", "ナカムラ", "コバヤシ", "カトウ",
            "ヨシダ", "ヤマダ", "マツモト", "イノウエ", "キムラ", "ハヤシ", "シミズ", "ヤマザキ", "モリ", "イケダ",
            "ハシモト", "イシカワ", "マエダ", "フジタ", "オカダ", "ゴトウ", "ハセガワ", "ムラカミ"]
MEI = [("健一", "ケンイチ"), ("美咲", "ミサキ"), ("大輔", "ダイスケ"), ("陽子", "ヨウコ"), ("拓也", "タクヤ"),
       ("由美", "ユミ"), ("翔太", "ショウタ"), ("真理", "マリ"), ("誠", "マコト"), ("恵", "メグミ")]
STATIONS = ["SS青葉台", "SS港北", "SS中央通り", "SS北口", "SS駅前", "SSバイパス", "SS本町", "SS緑ヶ丘"]


def _card_no(rng: random.Random, used: set[str]) -> str:
    while True:
        n = "4980" + "".join(str(rng.randint(0, 9)) for _ in range(12))
        if n not in used:
            used.add(n)
            return n


def build_data(month: str, seed: int = 42) -> dict:
    rng = random.Random(seed)
    m_start, m_end = month_start(month), month_end(month)
    prev_end = m_start - timedelta(days=1)

    facilities = [{"施設コード": f"F{i + 1:03d}", "施設名": name, "部門コード": f"{101 + i}", "_kana": kana}
                  for i, (name, kana) in enumerate(FACILITIES)]
    fac_codes = [f["施設コード"] for f in facilities]

    used_nos: set[str] = set()
    ledger: list[dict] = []
    # 施設車両カード 172枚
    counters: dict[str, int] = {}
    for _ in range(172):
        fac = rng.choice(facilities)
        counters[fac["施設コード"]] = counters.get(fac["施設コード"], 0) + 1
        ledger.append({
            "カード番号": format_card_no(_card_no(rng, used_nos)), "施設コード": fac["施設コード"],
            "施設名": fac["施設名"], "区分": "施設", "社員番号": "", "利用者": f"公用車{counters[fac['施設コード']]}",
            "カード名義": f"{fac['_kana']} {counters[fac['施設コード']]}",
            "有効開始日": date(2024, 4, 1), "有効終了日": None, "備考": "",
        })
    # AM 14名・アドバイザー 14名（個人カード）
    employees = []
    for i in range(28):
        sei, mei = SEI[i], MEI[i % len(MEI)]
        cat = "AM" if i < 14 else "アドバイザー"
        emp = {"社員番号": f"E{1001 + i}", "氏名": f"{sei} {mei[0]}", "kana": f"{SEI_KANA[i]} {mei[1]}", "区分": cat}
        employees.append(emp)
        home = rng.choice(facilities)  # 個人カードの台帳上の所属（按分するので参考値）
        ledger.append({
            "カード番号": format_card_no(_card_no(rng, used_nos)), "施設コード": home["施設コード"],
            "施設名": home["施設名"], "区分": cat, "社員番号": emp["社員番号"], "利用者": emp["氏名"],
            "カード名義": emp["kana"], "有効開始日": date(2024, 4, 1), "有効終了日": None, "備考": "",
        })

    # --- カード情報の変更履歴（3枚：先月まで別施設、当月から新施設）
    for idx in (3, 40, 90):
        row = ledger[idx]
        old_fac = next(f for f in facilities if f["施設コード"] != row["施設コード"])
        old = dict(row, 施設コード=old_fac["施設コード"], 施設名=old_fac["施設名"],
                   有効終了日=prev_end, 備考="旧所属")
        row["有効開始日"] = m_start
        row["備考"] = f"{m_start:%Y/%m/%d} {old_fac['施設名']}から移管"
        ledger.append(old)

    # --- わざと混ぜるエラー
    ledger[10]["施設コード"] = "F099"                    # L003 施設マスタにない
    ledger[11]["カード名義"] = "キュウメイギ タロウ"         # M003 名義不一致（PDF側は正しい名義）
    true_holder_11 = f"{facilities[fac_codes.index(ledger[11]['施設コード'])]['_kana']} " \
                     f"{ledger[11]['利用者'].replace('公用車', '')}"
    ledger[12]["有効終了日"] = date(m_start.year, m_start.month, 1) - timedelta(days=40)  # M002 期間外だが利用あり

    # --- 給与按分資料
    alloc_rows = []
    missing_emp = employees[2]["社員番号"]   # A001 按分資料にいないAM
    bad_sum_emp = employees[20]["社員番号"]  # A002 合計90%
    for emp in employees:
        if emp["社員番号"] == missing_emp:
            continue
        n = rng.randint(2, 5)
        targets = rng.sample(fac_codes, n)
        if n == 3 and rng.random() < 0.5:
            ratios = [33.33, 33.33, 33.34]
        else:
            cuts = sorted(rng.sample(range(5, 100, 5), n - 1))
            ratios = [b - a for a, b in zip([0] + cuts, cuts + [100])]
        if emp["社員番号"] == bad_sum_emp:
            ratios[-1] -= 10
        for fc, r in zip(targets, ratios):
            alloc_rows.append({"対象月": month, "社員番号": emp["社員番号"], "氏名": emp["氏名"],
                               "按分先施設コード": fc, "按分割合": r})

    # --- 利用明細（PDFに載せる内容）
    active = [r for r in ledger if r["備考"] != "旧所属"]
    planted = {10, 11, 12, 172 + 2, 172 + 20}  # エラーを仕込んだカードは必ず利用ありにする
    unused = set(rng.sample(sorted(set(range(len(active))) - planted), 5))
    cards_on_pdf = []
    for i, r in enumerate(active):
        if i in unused:
            continue
        holder = true_holder_11 if i == 11 else r["カード名義"]
        cards_on_pdf.append({"card_no": r["カード番号"], "holder": holder})
    for kana in ("ミトウロク イチ", "ミトウロク ニ"):  # M001 台帳にないカード
        cards_on_pdf.append({"card_no": format_card_no(_card_no(rng, used_nos)), "holder": kana})
    rng.shuffle(cards_on_pdf)

    days = (m_end - m_start).days + 1
    for c in cards_on_pdf:
        c["lines"] = []
        for _ in range(rng.randint(1, 5)):
            diesel = rng.random() < 0.2
            liters = round(rng.uniform(15, 55), 2)
            price = rng.randint(148, 158) if diesel else rng.randint(168, 178)
            d = m_start + timedelta(days=rng.randrange(days))
            c["lines"].append({"date": f"{d:%m/%d}", "station": rng.choice(STATIONS),
                               "item": "軽油" if diesel else "レギュラー", "liters": liters,
                               "amount": int(round(liters * price))})
        c["lines"].sort(key=lambda x: x["date"])
        c["subtotal"] = sum(x["amount"] for x in c["lines"])
    # P001 読取不能な行（PDF上で表記が崩れている想定）
    cards_on_pdf[7]["lines"][0]["garbled"] = True

    return {
        "facilities": facilities, "ledger": ledger, "allocation": alloc_rows, "cards": cards_on_pdf,
        "employees": employees,
        "answer": {
            "grand_total": sum(c["subtotal"] for c in cards_on_pdf),
            "garbled_card": cards_on_pdf[7]["card_no"],
            "garbled_amount": cards_on_pdf[7]["lines"][0]["amount"],
            "unregistered_cards": [c["card_no"] for c in cards_on_pdf if c["holder"].startswith("ミトウロク")],
            "bad_facility_card": ledger[10]["カード番号"],
            "holder_mismatch_card": ledger[11]["カード番号"],
            "expired_card": ledger[12]["カード番号"],
            "missing_alloc_emp": missing_emp,
            "bad_sum_emp": bad_sum_emp,
        },
    }


def _font() -> str:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    for path, idx in (("C:/Windows/Fonts/msgothic.ttc", 0), ("C:/Windows/Fonts/meiryo.ttc", 0)):
        if Path(path).exists():
            pdfmetrics.registerFont(TTFont("JP", path, subfontIndex=idx))
            return "JP"
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    pdfmetrics.registerFont(UnicodeCIDFont("HeiseiKakuGo-W5"))
    return "HeiseiKakuGo-W5"


def write_pdf(data: dict, month: str, path: Path) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    font = _font()
    W, H = A4
    cv = canvas.Canvas(str(path), pagesize=A4)
    y_top, y_bottom, lh = H - 60, 60, 14
    page = 1

    def header(first: bool) -> float:
        cv.setFont(font, 9)
        cv.drawString(40, H - 30, "【サンプル・架空データ】 法人ガソリンカード ご利用代金明細書")
        cv.drawRightString(W - 40, H - 30, f"{page} ページ")
        y = y_top
        if first:
            y_, m_ = month.split("-")
            cv.setFont(font, 14)
            cv.drawString(40, y, "ご利用代金明細書")
            y -= 22
            cv.setFont(font, 10)
            cv.drawString(40, y, f"ご利用月: {y_}年{int(m_)}月分")
            y -= lh
            cv.drawString(40, y, "契約者: サンプル株式会社 御中")
            y -= lh
            cv.drawString(40, y, f"ご請求合計金額 ¥{data['answer']['grand_total']:,}")
            y -= lh * 2
        return y

    y = header(True)
    for c in data["cards"]:
        need = (len(c["lines"]) + 3) * lh
        if y - need < y_bottom:
            cv.showPage()
            page += 1
            y = header(False)
        cv.setFont(font, 10)
        cv.drawString(40, y, f"カード番号 {c['card_no']}  名義 {c['holder']}")
        y -= lh
        cv.setFont(font, 9)
        for ln in c["lines"]:
            if ln.get("garbled"):
                liters = str(ln["liters"]).translate(str.maketrans("0123456789.", "０１２３４５６７８９．"))
                a = f"{ln['amount']:,}"
                amount = f"¥ {a[:-2]} {a[-2:]}"  # 表記崩れ（余計な空白・記号）
            else:
                liters, amount = f"{ln['liters']:.2f}", f"{ln['amount']:,}"
            cv.drawString(60, y, ln["date"])
            cv.drawString(110, y, ln["station"])
            cv.drawString(230, y, ln["item"])
            cv.drawRightString(380, y, f"{liters}L")
            cv.drawRightString(500, y, amount)
            y -= lh
        cv.setFont(font, 9)
        cv.drawString(230, y, "カード小計")
        cv.drawRightString(500, y, f"{c['subtotal']:,}")
        y -= lh * 1.6
    cv.save()


def write_excels(data: dict, month: str, ledger_path: Path, alloc_path: Path) -> None:
    led = pd.DataFrame(data["ledger"])
    fac = pd.DataFrame(data["facilities"]).drop(columns="_kana")
    with pd.ExcelWriter(ledger_path, engine="openpyxl") as w:
        led.to_excel(w, sheet_name="カード台帳", index=False)
        fac.to_excel(w, sheet_name="施設マスタ", index=False)
    with pd.ExcelWriter(alloc_path, engine="openpyxl") as w:
        pd.DataFrame(data["allocation"]).to_excel(w, sheet_name="按分", index=False)


def make_dummy(out_dir: Path, month: str, seed: int = 42) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    data = build_data(month, seed)
    ym = month.replace("-", "")
    write_pdf(data, month, out_dir / f"利用明細_{ym}.pdf")
    write_excels(data, month, out_dir / f"管理台帳_{ym}.xlsx", out_dir / f"給与按分_{ym}.xlsx")
    return data


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", default="2026-10")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    out = ROOT / "data" / "input" / args.month
    d = make_dummy(out, args.month, args.seed)
    print(f"作成しました: {out}")
    print("仕込んだエラーの答え:")
    print(json.dumps(d["answer"], ensure_ascii=False, indent=2))
