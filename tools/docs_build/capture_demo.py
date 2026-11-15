"""操作デモの撮影：起動中のアプリ（localhost:8501）を実際に操作し、各場面をPNGに保存する

撮影後は修正ファイルを消して、ダミーデータを「エラー入り」の状態に戻す。
"""
from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
SHOTS = ROOT / "data" / "video_work" / "demo"
CORR = ROOT / "data" / "corrections" / "2026-10.json"
SHOTS.mkdir(parents=True, exist_ok=True)
log: dict[str, str] = {}

HL_JS = """([x, y, w, h, label]) => {
  const d = document.createElement('div'); d.className = '__hl';
  Object.assign(d.style, {position:'fixed', left:(x-6)+'px', top:(y-6)+'px', width:(w+12)+'px', height:(h+12)+'px',
    border:'5px solid #f59e0b', borderRadius:'10px', boxShadow:'0 0 0 9999px rgba(15,23,42,.28)', zIndex:2147483647, pointerEvents:'none'});
  if (label) { const t = document.createElement('div'); t.textContent = label;
    Object.assign(t.style, {position:'absolute', left:'0', top:(h+18)+'px', background:'#f59e0b', color:'#111', font:'700 22px sans-serif',
      padding:'6px 14px', borderRadius:'8px', whiteSpace:'nowrap'});
    if (y + h + 70 > window.innerHeight) t.style.top = '-52px';
    d.appendChild(t); }
  document.body.appendChild(d);
}"""


boxes: dict[str, list[float] | None] = {}


def shot(pg: Page, name: str, target=None, label: str = "") -> None:
    """印なしの画面（*_plain.png）と押す場所の座標、印つきの画面（*.png）を保存"""
    b = None
    if target is not None:
        target.scroll_into_view_if_needed()
        pg.wait_for_timeout(300)
        b = target.bounding_box()
    pg.wait_for_timeout(250)
    pg.screenshot(path=SHOTS / f"{name}_plain.png")
    boxes[name] = [b["x"], b["y"], b["width"], b["height"]] if b else None
    if b:
        pg.evaluate(HL_JS, [b["x"], b["y"], b["width"], b["height"], label])
    pg.wait_for_timeout(150)
    pg.screenshot(path=SHOTS / f"{name}.png")
    pg.evaluate("document.querySelectorAll('.__hl').forEach(e => e.remove())")
    log[name] = metric(pg)


def metric(pg: Page) -> str:
    try:
        return " / ".join(pg.locator('[data-testid="stMetric"]').all_inner_texts()).replace("\n", " ")
    except Exception:
        return ""


def wait_idle(pg: Page) -> None:
    pg.wait_for_timeout(1500)
    pg.locator('[data-testid="stSpinner"]').first.wait_for(state="detached", timeout=180000)
    pg.wait_for_timeout(2000)


def expander(pg: Page, code: str):
    return pg.locator('[data-testid="stExpander"]').filter(has_text=f"[{code}]").first


def fix_with_button(pg: Page, code: str, button: str, name: str, label: str) -> None:
    exp = expander(pg, code)
    exp.locator("summary").click()
    pg.wait_for_timeout(800)
    btn = exp.get_by_role("button", name=button)
    shot(pg, name, btn, label)
    btn.click()
    wait_idle(pg)


def main() -> None:
    CORR.unlink(missing_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge")
        pg = b.new_page(viewport={"width": 1600, "height": 860})
        pg.goto("http://localhost:8501", wait_until="networkidle")
        pg.wait_for_timeout(2500)
        shot(pg, "01_start")

        month = pg.get_by_role("textbox", name="対象月（利用月）")
        month.fill("2026-10"); month.press("Enter"); pg.wait_for_timeout(800)
        shot(pg, "02_month", month, "対象月を入力")
        who = pg.get_by_role("textbox", name="担当者名（修正履歴に残ります）")
        who.fill("山田"); who.press("Enter"); pg.wait_for_timeout(800)
        shot(pg, "03_who", who, "担当者名を入力")

        upl = pg.locator('[data-testid="stExpander"]').filter(has_text="入力ファイルを置く").first
        upl.locator("summary").click(); pg.wait_for_timeout(1000)
        shot(pg, "04_upload", upl.get_by_role("button", name="保存する"), "3つのファイルを選んで保存")
        upl.locator("summary").click(); pg.wait_for_timeout(600)

        run = pg.get_by_role("button", name="▶ 実行（再集計）")
        shot(pg, "05_run", run, "クリック")
        run.click()
        pg.wait_for_timeout(1200)
        shot(pg, "06_running")
        wait_idle(pg)
        pg.evaluate("window.scrollTo(0,0)")
        shot(pg, "07_result", pg.locator('[data-testid="stMetric"]').nth(3), "エラー件数")
        shot(pg, "08_list", pg.locator('[data-testid="stDataFrame"]').first, "エラーの一覧")

        fix_with_button(pg, "M001", "このカードの情報を確定", "09_fix_card", "施設を選んで確定")
        shot(pg, "10_after_fix", pg.locator('[data-testid="stMetric"]').nth(3), "エラーが減った")
        fix_with_button(pg, "M001", "このカードの情報を確定", "11_fix_card2", "確定")
        fix_with_button(pg, "M002", "このカードの情報を確定", "12_fix_expired", "期間外カードも施設を指定")
        fix_with_button(pg, "L003", "このカードの情報を確定", "13_fix_facility", "正しい施設を選んで確定")

        # A002：割合の合計が90%のため確定ボタンが押せない状態を撮影
        exp = expander(pg, "A002")
        exp.locator("summary").click(); pg.wait_for_timeout(1000)
        shot(pg, "14_ratio_ng", exp.get_by_role("button", name="按分を確定"), "合計100%になるまで押せない")
        # 表の割合を書き換える代わりに、画面と同じ保存処理で正しい割合を登録
        import sys
        sys.path.insert(0, str(ROOT))
        from src.common import load_config
        from src.pipeline import load_corrections, save_correction
        cfg = load_config()
        plan = None
        for line in [ln for ln in (ROOT / "data" / "input" / "2026-10").glob("給与按分_*.xlsx")]:
            import pandas as pd
            df = pd.read_excel(line, dtype=str)
            g = df[df["社員番号"] == "E1021"]
            plan = [{"facility_code": r["按分先施設コード"], "ratio": float(r["按分割合"])} for _, r in g.iterrows()]
            plan[-1]["ratio"] += 100 - sum(x["ratio"] for x in plan)
        save_correction(cfg, "2026-10", "alloc_overrides", "E1021", plan, "山田", "A002: 按分割合を100%に修正")
        pg.get_by_role("button", name="▶ 実行（再集計）").click()
        wait_idle(pg)

        fix_with_button(pg, "A001", "按分を確定", "15_fix_alloc", "按分先と割合を入れて確定")
        # 再集計が終わってエラー0件になるまで待つ（押し損ねたらもう一度押す）
        for _ in range(3):
            try:
                pg.locator('[data-testid="stMetric"]').nth(3).filter(has_text="0 件").wait_for(timeout=60000)
                break
            except Exception:
                exp = expander(pg, "A001")
                if exp.count():
                    exp.locator("summary").click(); pg.wait_for_timeout(800)
                    exp.get_by_role("button", name="按分を確定").click()
                wait_idle(pg)
        pg.wait_for_timeout(1500)
        pg.evaluate("window.scrollTo(0,0)")
        shot(pg, "16_all_clear", pg.locator('[data-testid="stAlert"]').first, "エラー0件")

        pg.get_by_role("tab", name="✅ 整合性チェック").click(); pg.wait_for_timeout(1200)
        shot(pg, "17_check", pg.locator('[data-testid="stTable"]').first, "3つの合計が一致")
        pg.get_by_role("tab", name="🏢 施設別集計").click(); pg.wait_for_timeout(1500)
        shot(pg, "18_facility")
        pg.get_by_role("tab", name="📥 出力").click(); pg.wait_for_timeout(1500)
        shot(pg, "19_output", pg.get_by_role("button", name="⬇ 費用振替CSV").first, "ダウンロード")
        pg.get_by_role("tab", name="📝 修正履歴").click(); pg.wait_for_timeout(1500)
        shot(pg, "20_history", pg.get_by_role("button", name="取消").first, "間違えたら取消")
        b.close()

    hist = load_corrections(cfg, "2026-10")["history"]
    (SHOTS / "boxes.json").write_text(json.dumps(boxes, ensure_ascii=False, indent=1), encoding="utf-8")
    (SHOTS / "log.json").write_text(json.dumps({"metrics": log, "history_count": len(hist)},
                                               ensure_ascii=False, indent=2), encoding="utf-8")
    CORR.unlink(missing_ok=True)  # デモ用の修正を消して元の状態に戻す
    print(json.dumps(log, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
