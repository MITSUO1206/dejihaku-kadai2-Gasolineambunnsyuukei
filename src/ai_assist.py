"""AI読取補助（Claude API）

ルールで読めなかった明細行だけを Claude に渡し、日付・給油所・品目・数量・金額を抜き出してもらう。

安全のための約束:
  - 送るのは「読めなかった行の文字列」だけ。カード番号・名義・施設名は送らない（行IDで対応づけ）
  - AIの値はそのまま信用しない。カード小計と一致したときだけ採用し、「要確認(P005)」として残す
  - 送信内容と応答は data/logs に保存する（後から監査できるように）
  - APIキー未設定・通信エラー時はAIなしで続行し、通常の読取エラー(P001)として扱う
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime

from .common import ROOT
from .errors import Issue

SYSTEM = (
    "あなたはガソリンカード利用明細PDFの読取を補助します。"
    "PDFから取り出した文字列のうち、プログラムで読めなかった明細行が渡されます。"
    "各行から 利用日(MM/DD)・給油所名・品目・数量(L)・金額(円, 整数) を抜き出してください。"
    "全角数字・余分な空白・円記号は正規化してください。"
    "文字が欠けていて値を確定できない項目は null にし、推測で埋めないでください。"
    "明細行ではない（見出し・注記など）と判断した行は is_usage_line を false にしてください。"
)

SCHEMA = {
    "type": "object",
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "line_id": {"type": "string"},
                    "is_usage_line": {"type": "boolean"},
                    "date": {"type": ["string", "null"]},
                    "station": {"type": ["string", "null"]},
                    "item": {"type": ["string", "null"]},
                    "liters": {"type": ["number", "null"]},
                    "amount": {"type": ["integer", "null"]},
                    "note": {"type": "string"},
                },
                "required": ["line_id", "is_usage_line", "date", "station", "item", "liters", "amount", "note"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["lines"],
    "additionalProperties": False,
}


def _make_client():
    import anthropic
    # 応答が来ないときに画面が固まらないよう、待ち時間に上限を設ける
    return anthropic.Anthropic(timeout=90, max_retries=2)


def ask_claude(items: list[dict], cfg: dict, client=None) -> list[dict]:
    """items: [{"line_id": "L1", "text": "..."}] → Claude の抽出結果リスト"""
    client = client or _make_client()
    prompt = "次の明細行を読み取ってください。\n" + "\n".join(f"{x['line_id']}: {x['text']}" for x in items)
    response = client.beta.messages.create(
        model=cfg["ai"]["model"],
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",  # 安全判定で断られた場合はサーバー側で別モデルに切替
        system=SYSTEM,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": prompt}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("AIが応答を拒否しました")
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)["lines"]


def _path(cfg: dict, name: str):
    return ROOT / cfg["paths"]["logs_dir"] / name


def _key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return default


def _write_json(path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def daily_limit() -> int:
    """1日に実際にAPIへ問い合わせる回数の上限。0 = 上限なし。公開デモは既定20回"""
    from . import demo
    raw = os.environ.get("GASCARD_AI_DAILY_LIMIT")
    if raw is not None and raw.strip().isdigit():
        return int(raw)
    return 20 if demo.enabled() else 0


def _take_daily_quota(cfg: dict) -> bool:
    limit = daily_limit()
    if limit == 0:
        return True
    p = _path(cfg, "ai_usage.json")
    today = datetime.now().strftime("%Y-%m-%d")
    usage = _read_json(p, {})
    if usage.get("date") != today:
        usage = {"date": today, "calls": 0}
    if usage["calls"] >= limit:
        return False
    usage["calls"] += 1
    _write_json(p, usage)
    return True


def _log(entry: dict, cfg: dict) -> None:
    log_dir = ROOT / cfg["paths"]["logs_dir"]
    log_dir.mkdir(parents=True, exist_ok=True)
    with open(log_dir / "ai_requests.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def repair_unreadable_lines(st, cfg: dict, client=None) -> None:
    """Statement の unreadable 行を AI で補完する。小計と一致したカードだけ採用"""
    targets = [c for c in st.cards.values() if c.unreadable and c.override_amount is None]
    items, owner = [], {}
    for c in targets:
        for ln in c.unreadable:
            lid = f"L{len(items) + 1}"
            items.append({"line_id": lid, "text": ln})
            owner[lid] = (c, ln)
    items = items[: cfg["ai"]["max_lines_per_request"]]
    if not items:
        return

    # 同じ行は前回の答えを使う（料金を使わない）。採用するかは毎回、小計との検算で決める
    cache = _read_json(_path(cfg, "ai_cache.json"), {})
    results = [{**cache[_key(x["text"])], "line_id": x["line_id"]} for x in items if _key(x["text"]) in cache]
    pending = [x for x in items if _key(x["text"]) not in cache]
    entry = {"time": datetime.now().isoformat(timespec="seconds"), "sent": pending,
             "from_cache": len(items) - len(pending)}
    if pending:
        if not _take_daily_quota(cfg):
            entry["error"] = "1日の問い合わせ上限に達したため、AIを使わずに続行"
        else:
            try:
                got = ask_claude(pending, cfg, client)
                entry["received"] = got
                text_of = {x["line_id"]: x["text"] for x in pending}
                for r in got:
                    if r["line_id"] in text_of:
                        cache[_key(text_of[r["line_id"]])] = {k: v for k, v in r.items() if k != "line_id"}
                _write_json(_path(cfg, "ai_cache.json"), cache)
                results += got
            except Exception as e:  # APIキー未設定・通信断など。AIなしで続行する
                entry["error"] = f"{type(e).__name__}: {e}"
    _log(entry, cfg)
    st.ai_log.append(entry)

    by_card: dict[str, list[tuple[str, dict]]] = {}
    for r in results:
        if r["line_id"] in owner:
            c, ln = owner[r["line_id"]]
            by_card.setdefault(c.card_no, []).append((ln, r))

    for c in targets:
        got = by_card.get(c.card_no, [])
        usage = [(ln, r) for ln, r in got if r["is_usage_line"]]
        if len(got) != len(c.unreadable) or any(r["amount"] is None for _, r in usage):
            continue  # 読み切れなかった → P001 のまま
        candidate = c.amount + sum(r["amount"] for _, r in usage)
        if c.subtotal is None or candidate != c.subtotal:
            continue  # 検算が合わない値は採用しない
        for ln, r in usage:
            c.lines.append({"date": r["date"], "station": r["station"], "item": r["item"],
                            "liters": r["liters"], "amount": r["amount"], "source": "AI"})
            st.issues.append(Issue("P005", f"{c.page}ページ「{ln}」→ {r['amount']:,}円（{r['note']}）",
                                   card_no=c.card_no, user=c.holder, amount=r["amount"]))
        c.unreadable = []
