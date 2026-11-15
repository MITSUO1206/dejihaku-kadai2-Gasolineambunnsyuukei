"""動画用：最新の手順式の画面を実際に操作して撮る（デモモードのアプリを使う）

使い方:  デモモードで起動しておく（例：GASCARD_DEMO=1 で streamlit run app.py --server.port 8505）
         python tools/docs_build/capture_demo2.py [URL]
保存先:  data/video_work/demo2/<名前>_plain.png（印なし）と boxes.json（押す場所の座標）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "video_work" / "demo2"
OUT.mkdir(parents=True, exist_ok=True)
URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8505"
boxes: dict[str, list[float] | None] = {}


def idle(pg: Page) -> None:
    pg.wait_for_timeout(1000)
    pg.wait_for_function("""() => !document.querySelector('[data-testid="stSpinner"]')
        && !/Stop|Running/.test(document.querySelector('[data-testid="stStatusWidget"]')?.innerText || '')""",
                         timeout=240000)
    pg.wait_for_timeout(1500)


def shot(pg: Page, name: str, target=None) -> None:
    b = None
    if target is not None:
        target.scroll_into_view_if_needed()
        pg.wait_for_timeout(400)
        b = target.bounding_box()
    pg.screenshot(path=OUT / f"{name}_plain.png")
    boxes[name] = [b["x"], b["y"], b["width"], b["height"]] if b else None
    print("shot", name)


def pick_month(pg: Page, label: str) -> None:
    pg.get_by_label("対象月（カードを使った月）").click(); pg.wait_for_timeout(500)
    pg.get_by_role("option", name=label).click(); idle(pg)


def scroll_to(pg: Page, loc) -> None:
    loc.scroll_into_view_if_needed(); pg.wait_for_timeout(300)
    pg.mouse.wheel(0, 250); pg.wait_for_timeout(500)


with sync_playwright() as p:
    br = p.chromium.launch(channel="msedge")
    pg = br.new_page(viewport={"width": 1600, "height": 860})
    for _ in range(40):
        try:
            pg.goto(URL, wait_until="networkidle"); break
        except Exception:
            pg.wait_for_timeout(1000)
    pg.get_by_role("button", name="ログイン").wait_for(timeout=60000); idle(pg)
    pg.get_by_role("textbox", name="ユーザー名").fill("demo_staff")
    pg.get_by_role("textbox", name="パスワード").fill("demo1234")
    shot(pg, "n01_login", pg.get_by_role("button", name="ログイン"))
    pg.get_by_role("button", name="ログイン").click(); idle(pg)
    pg.get_by_text("毎月の集計").first.wait_for(timeout=60000); pg.wait_for_timeout(2500)
    shot(pg, "n02_month", pg.get_by_label("対象月（カードを使った月）"))
    pick_month(pg, "2026年10月")
    exp = pg.get_by_text("ファイルを置き直す").first
    exp.click(); idle(pg)
    shot(pg, "n03_files", pg.locator('[data-testid="stFileUploader"]').first)
    exp.click(); idle(pg)
    pg.evaluate("window.scrollTo(0,0)"); pg.locator('[data-testid="stMain"]').evaluate("e => e.scrollTo(0,0)")
    shot(pg, "n04_run", pg.get_by_role("button", name="▶ 集計を実行する"))
    pg.get_by_role("button", name="▶ 集計を実行する").click(); idle(pg)
    pg.locator('[data-testid="stMain"]').evaluate("e => e.scrollTo(0,0)"); pg.wait_for_timeout(500)
    shot(pg, "n05_steps", pg.locator('[data-testid="stHorizontalBlock"]').filter(has_text="1. ファイルを置く").last)
    warn = pg.get_by_text("直す必要があるエラーが").first
    shot(pg, "n06_remaining", warn)
    # 1件目：施設を選んで確定
    sel = pg.get_by_label("使った施設").first
    scroll_to(pg, sel)
    sel.click(); pg.wait_for_timeout(500); pg.get_by_role("option", name="F002 さくら館").click(); idle(pg)
    btn = pg.get_by_role("button", name="この内容で確定").first
    shot(pg, "n07_fix", btn)
    btn.click(); idle(pg)
    info = pg.get_by_text("1件直りました").first
    shot(pg, "n08_changed", info)
    # 按分のカード
    card = pg.get_by_text("按分の割合を足しても100%になりません").first
    scroll_to(pg, card)
    shot(pg, "n09_alloc", pg.get_by_text("いまの合計").first)
    # 直した内容（取り消し）
    undo_exp = pg.get_by_text("これまでに直した内容").first
    scroll_to(pg, undo_exp); undo_exp.click(); idle(pg)
    shot(pg, "n10_undo", pg.get_by_role("button", name="取り消す").first)
    # 11月：CSV
    pg.locator('[data-testid="stMain"]').evaluate("e => e.scrollTo(0,0)")
    pick_month(pg, "2026年11月")
    pg.get_by_role("button", name="▶ 集計を実行する").click(); idle(pg)
    pg.locator('[data-testid="stMain"]').evaluate("e => e.scrollTo(0,0)"); pg.wait_for_timeout(500)
    shot(pg, "n11_csv", pg.get_by_role("button", name="⬇ 費用振替CSVをダウンロード").first)
    pg.get_by_role("button", name="⬇ 費用振替CSVをダウンロード").first.click(); idle(pg)
    pg.locator('[data-testid="stMain"]').evaluate("e => e.scrollTo(0,0)"); pg.wait_for_timeout(500)
    shot(pg, "n12_done", pg.get_by_text("CSVを保存済み").first)
    pg.get_by_text("月次推移", exact=True).first.click(); idle(pg)
    shot(pg, "n13_trend", pg.get_by_text("月ごとの合計").first)
    br.close()

(OUT / "boxes.json").write_text(json.dumps(boxes, ensure_ascii=False, indent=1), encoding="utf-8")
print("saved", OUT)
