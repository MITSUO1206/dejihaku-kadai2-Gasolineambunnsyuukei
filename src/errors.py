"""エラー定義。各ステップで見つかった問題をこの形式で記録する"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

ERROR = "エラー"      # CSV出力を止める
WARNING = "要確認"    # 出力は止めないが目視確認する
INFO = "情報"

# コード: (重要度, ステップ, 内容, 対処方法)
CATALOG: dict[str, tuple[str, str, str, str]] = {
    "L001": (ERROR, "①台帳", "台帳の必須項目が空欄", "台帳を修正して再取込、または画面でカード情報を補正"),
    "L002": (ERROR, "①台帳", "同じカード番号が同じ期間に複数行ある", "台帳の有効開始日・終了日を修正"),
    "L003": (ERROR, "①台帳", "施設コードが施設マスタにない", "台帳の施設コードか施設マスタを修正"),
    "L004": (ERROR, "①台帳", "区分が想定外（施設/AM/アドバイザー以外）", "台帳の区分を修正"),
    "P001": (ERROR, "②PDF", "PDFの行を読み取れなかった", "PDFを目視確認し、画面で金額を入力"),
    "P002": (ERROR, "②PDF", "カード小計と明細の合計が一致しない", "PDFを目視確認し、画面で金額を補正"),
    "P003": (ERROR, "②PDF", "請求合計と読取金額の合計が一致しない", "読取漏れの行を確認"),
    "P004": (ERROR, "②PDF", "PDFから請求合計を読み取れなかった", "PDFの様式を確認"),
    "P005": (WARNING, "②PDF", "AIが読み取った行（小計との一致を確認済み）", "PDF原本と見比べて確認"),
    "M001": (ERROR, "③照合", "明細のカード番号が台帳にない", "台帳に追加して再取込、または画面で利用施設を指定"),
    "M002": (ERROR, "③照合", "台帳にあるが対象月は有効期間外", "台帳の有効期間を確認（変更・解約漏れ）"),
    "M003": (WARNING, "③照合", "PDFのカード名義と台帳のカード名義が違う", "台帳の名義が最新か確認"),
    "M004": (ERROR, "③照合", "按分対象者なのに社員番号がない", "台帳に社員番号を入力"),
    "M005": (INFO, "③照合", "台帳で有効だが当月の利用がないカード", "対応不要（休眠カードの確認用）"),
    "A001": (ERROR, "④按分", "給与按分資料に該当者がいない", "按分資料を更新、または画面で按分先を指定"),
    "A002": (ERROR, "④按分", "按分割合の合計が100%ではない", "按分資料の割合を修正"),
    "A003": (ERROR, "④按分", "按分先の施設コードが施設マスタにない", "按分資料の施設コードを修正"),
    "C001": (ERROR, "⑤整合性", "明細合計・按分後合計・CSV合計が一致しない", "システム不具合の可能性。担当者へ連絡"),
}


@dataclass
class Issue:
    code: str
    detail: str
    card_no: str = ""
    facility: str = ""
    user: str = ""
    amount: int | None = None
    ref: dict = field(default_factory=dict)  # 修正画面で使う補助情報

    @property
    def severity(self) -> str:
        return CATALOG[self.code][0]

    @property
    def step(self) -> str:
        return CATALOG[self.code][1]

    @property
    def title(self) -> str:
        return CATALOG[self.code][2]

    @property
    def action(self) -> str:
        return CATALOG[self.code][3]

    def to_row(self) -> dict:
        return {
            "重要度": self.severity,
            "ステップ": self.step,
            "コード": self.code,
            "内容": self.title,
            "詳細": self.detail,
            "カード番号": self.card_no,
            "施設": self.facility,
            "利用者": self.user,
            "金額": self.amount,
            "対処方法": self.action,
        }

    def to_dict(self) -> dict:
        return asdict(self)
