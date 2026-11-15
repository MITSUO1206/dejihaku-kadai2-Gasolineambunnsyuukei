"""起動中のアプリ画面（http://localhost:8501）を撮影して 資料/img/ に保存する"""
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
IMG = ROOT / "資料" / "img"
IMG.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge")
    pg = b.new_page(viewport={"width": 1600, "height": 1000}, device_scale_factor=1.5)
    pg.goto("http://localhost:8501", wait_until="networkidle")
    pg.get_by_role("button", name="▶ 実行（再集計）").click()
    pg.get_by_text("分 ガソリンカード集計").wait_for(timeout=120000)
    pg.wait_for_timeout(2500)
    pg.screenshot(path=IMG / "app_main.png")

    pg.get_by_text("[M001]").first.click()
    pg.wait_for_timeout(1200)
    pg.get_by_text("[M001]").first.scroll_into_view_if_needed()
    pg.wait_for_timeout(800)
    pg.screenshot(path=IMG / "app_fix.png")

    pg.get_by_role("tab", name="✅ 整合性チェック").click()
    pg.wait_for_timeout(1200)
    pg.evaluate("window.scrollTo(0,0)")
    pg.screenshot(path=IMG / "app_check.png")
    b.close()
print("saved to", IMG)
