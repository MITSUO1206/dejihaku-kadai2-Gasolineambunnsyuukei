"""② 利用明細PDFの読取

1. pdfplumber で文字を取り出し、正規表現（ルール）で行を読む
2. ルールで読めなかった行だけを Claude API に送り、項目を抜き出してもらう（AI読取補助）
3. どちらで読んだ値も「カード小計」「請求合計」と突き合わせて検算する

※ 実際のトヨタファイナンス明細の様式に合わせるときは、PATTERNS を調整する。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber

from .common import nfkc, normalize_card_no, parse_amount
from .errors import Issue

PATTERNS = {
    "grand_total": re.compile(r"ご請求合計金額\s*¥?\s*([\d,]+)"),
    "card": re.compile(r"カード番号\s*([\d\- ]{16,23})\s+名義\s*(.*)$"),
    "line": re.compile(r"^(\d{1,2}/\d{1,2})\s+(\S+)\s+(\S+)\s+([\d.]+)L\s+([\d,]+)$"),
    "subtotal": re.compile(r"カード小計\s+([\d,]+)$"),
}
IGNORE = re.compile(r"(ページ$|ご利用代金明細書|ご利用月|契約者|^\s*$)")


@dataclass
class CardUsage:
    card_no: str
    holder: str
    page: int
    lines: list[dict] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)
    subtotal: int | None = None
    override_amount: int | None = None   # 画面で担当者が入力した金額

    @property
    def amount(self) -> int:
        if self.override_amount is not None:
            return self.override_amount
        return sum(ln["amount"] for ln in self.lines)


@dataclass
class Statement:
    path: Path
    grand_total: int | None
    cards: dict[str, CardUsage]
    issues: list[Issue] = field(default_factory=list)
    ai_log: list[dict] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(c.amount for c in self.cards.values())


def parse_text(pages: list[str], path: Path = Path("")) -> Statement:
    grand_total = None
    cards: dict[str, CardUsage] = {}
    cur: CardUsage | None = None
    for page_no, text in enumerate(pages, start=1):
        for raw in (text or "").splitlines():
            line = nfkc(raw)
            if m := PATTERNS["grand_total"].search(line):
                grand_total = parse_amount(m.group(1))
                continue
            if m := PATTERNS["card"].search(line):
                no = normalize_card_no(m.group(1))
                cur = cards.setdefault(no, CardUsage(no, m.group(2).strip(), page_no))
                continue
            if IGNORE.search(line) or cur is None:
                continue
            if m := PATTERNS["subtotal"].search(line):
                cur.subtotal = parse_amount(m.group(1))
                continue
            if m := PATTERNS["line"].match(line):
                cur.lines.append({"date": m.group(1), "station": m.group(2), "item": m.group(3),
                                  "liters": float(m.group(4)), "amount": parse_amount(m.group(5)),
                                  "source": "ルール"})
                continue
            cur.unreadable.append(line)
    return Statement(path=path, grand_total=grand_total, cards=cards)


def read_pdf(path: Path) -> list[str]:
    with pdfplumber.open(path) as pdf:
        return [p.extract_text() or "" for p in pdf.pages]


def validate(st: Statement) -> None:
    """読取結果の検算。エラーを st.issues に入れる"""
    st.issues = [i for i in st.issues if i.code == "P005"]
    for c in st.cards.values():
        if c.override_amount is not None:
            continue
        if c.unreadable:
            for ln in c.unreadable:
                st.issues.append(Issue("P001", f"{c.page}ページ: 「{ln}」", card_no=c.card_no, user=c.holder,
                                       ref={"subtotal": c.subtotal, "read_sum": c.amount}))
        elif c.subtotal is None or c.subtotal != c.amount:
            st.issues.append(Issue("P002", f"読取合計 {c.amount:,} / 小計 {c.subtotal}",
                                   card_no=c.card_no, user=c.holder, amount=c.amount,
                                   ref={"subtotal": c.subtotal, "read_sum": c.amount}))
    if st.grand_total is None:
        st.issues.append(Issue("P004", "「ご請求合計金額」の行が見つかりません"))
    elif not any(i.code in ("P001", "P002") for i in st.issues) and st.grand_total != st.total:
        st.issues.append(Issue("P003", f"請求合計 {st.grand_total:,} / 読取合計 {st.total:,}",
                               amount=st.total - st.grand_total))


def apply_overrides(st: Statement, pdf_overrides: dict) -> None:
    for card_no, ov in (pdf_overrides or {}).items():
        if card_no in st.cards:
            st.cards[card_no].override_amount = int(ov["amount"])


def load_statement(path: Path, cfg: dict, pdf_overrides: dict | None = None, use_ai: bool | None = None,
                   ai_client=None) -> Statement:
    st = parse_text(read_pdf(path), path)
    apply_overrides(st, pdf_overrides or {})
    ai_on = cfg["ai"]["enabled"] if use_ai is None else use_ai
    if ai_on and any(c.unreadable and c.override_amount is None for c in st.cards.values()):
        from .ai_assist import repair_unreadable_lines
        repair_unreadable_lines(st, cfg, client=ai_client)
    validate(st)
    return st
