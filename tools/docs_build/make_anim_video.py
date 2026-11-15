"""背景・After・実際の操作をアニメーションで見せる動画を作る（資料/背景とAfter_アニメーション動画.mp4）

anim.html のアニメーションを、ナレーションの時刻に合わせて1コマずつ描画して動画にする。
操作パートは capture_demo.py で撮った実際のアプリ画面（*_plain.png と boxes.json）を使う。

使い方:  cd tools\\docs_build; ..\\..\\.venv\\Scripts\\python make_anim_video.py [--preview]
"""
from __future__ import annotations

import json
import subprocess
import sys
import wave
from pathlib import Path

from playwright.sync_api import sync_playwright

from make_video import READ, ff, reading

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WORK = ROOT / "data" / "video_work" / "anim"
FRAMES = WORK / "frames"
DEMO = ROOT / "data" / "video_work" / "demo2"   # capture_demo2.py で撮った最新の画面
OUT = ROOT / "資料" / "システム紹介アニメーション.mp4"
FPS = 30
GAP = 0.5        # 文と文の間
LEAD = 0.6       # シーンの頭で、しゃべる前に見せる時間
LEAD_SCR = 0.9   # 操作画面：カーソルが動いてクリックするまで待つ
TAIL = 1.2

READ.update({"給与按分資料": "きゅうよあんぶんしりょう", "100パーセント": "ひゃくパーセント", "1円": "いちえん", "0件": "ぜろけん", "200枚": "にひゃくまい", "1枚": "いちまい", "1円": "いちえん", "3つ": "みっつ", "1枚ずつ": "いちまいずつ",
             "6件": "ろっけん", "0件": "ぜろけん", "1件": "いっけん", "2つ": "ふたつ"})

# 操作パート：(撮影名, 見出し, 画面上のラベル, ナレーション)
STEPS = [
    ("n01_login", "ログイン", "ログイン", "まず、担当者のIDとパスワードでログインします。"),
    ("n02_month", "対象月を選ぶ", "対象月", "画面の上で、集計する月を選びます。"),
    ("n03_files", "ファイルを置く", "ここに置く", "明細のPDFと、2つのExcelを置いて、保存します。"),
    ("n04_run", "集計を実行する", "クリック", "「集計を実行する」を押すと、照合と按分が自動で進みます。"),
    ("n05_steps", "いまの手順", "いまの手順", "上の表示で、今どの手順にいるかが分かります。"),
    ("n06_remaining", "エラーの件数", "残りの件数", "直す必要があるエラーの件数と、まだCSVに入っていない金額が表示されます。"),
    ("n07_fix", "エラーを直す", "選んで確定", "エラーごとに、使った施設を選んで「確定」を押します。選ぶまでは押せません。"),
    ("n08_changed", "何が変わったか", "変化の説明", "確定すると自動で集計し直し、何件直ったか、残り何件かを知らせます。"),
    ("n09_alloc", "按分の割合", "合計を確認", "按分の割合は、合計が100パーセントになると確定できます。"),
    ("n10_undo", "取り消し", "取り消せる", "間違えたときは、確認のうえで取り消せます。"),
    ("n11_csv", "CSVを出す", "ダウンロード", "エラーが0件になると、上にCSVのボタンが出ます。"),
    ("n12_done", "完了", "完了", "保存すると「完了」になり、いつ誰が出したかも記録されます。"),
    ("n13_trend", "月次推移", "月ごとの合計", "たまったデータは、月次推移で、施設ごとに比べられます。"),
]

SCRIPT: list[tuple[str, str, dict | None]] = [
    ("a", "このグループでは、たくさんの施設が、毎日、車を使っています。", None),
    ("a", "給油には、施設ごとに配られたガソリンカードを使います。その数、およそ200枚。", None),
    ("b", "ところが、カード会社からの請求書は、全部まとめて、本部に1枚だけ届きます。", None),
    ("b", "本部が全額を払うと、帳簿の上では、本部がガソリン代を使ったことになってしまいます。", None),
    ("c", "そこで、実際に使った施設ごとに、金額を振り分け直します。これが、費用振替です。", None),
    ("d", "この振り分けを、担当者が毎月、手作業で行っています。", None),
    ("d", "カード番号を台帳で調べて、金額を書き写す。これを200枚分、1枚ずつ。", None),
    ("e", "さらに、いくつもの施設を回るエリアマネージャーのガソリン代は、施設ごとに、割合で分ける計算も必要です。", None),
    ("f", "時間がかかるうえに、転記ミスや計算ミス、変更の反映漏れの心配も、つきまといます。", None),
    ("g", "この作業を、まるごと自動にしたのが、このシステムです。", None),
    ("p1", "システムの中では、まず3つのファイルを読み込みます。", None),
    ("p1", "明細PDFからはカード番号と金額、管理台帳からはどの施設のカードか、給与按分資料からは何パーセントずつ分けるかを読み取ります。", None),
    ("p2", "明細PDFは1行ずつ読み取り、カードごとに金額を集めます。", None),
    ("p2", "読めない行だけはAIが読み取り、カードの小計や請求合計と合うかを必ず確かめます。", None),
    ("p3", "次に、カード番号で管理台帳を探し、今月有効な行から、どの施設のカードかを決めます。", None),
    ("p3", "台帳に見つからないカードは、エラーとして担当者に知らせます。", None),
    ("p4", "エリアマネージャーのカードは、給与按分資料でその人の割合を引いて、施設ごとに分けます。", None),
    ("p4", "1円単位の端数も調整して、分けた後の合計は、必ず元の金額と一致させます。", None),
    ("p5", "そして施設ごとに、施設の車で使った分と、分けられた分を合計します。", None),
    ("p6", "最後に、請求合計、分けた後の合計、CSVの合計の3つが一致するかを確かめます。", None),
    ("p6", "一致したときだけ、会計システム用のCSVと、確認用のExcelを作ります。", None),
    ("h", "やることは、3つのファイルを入れて、ボタンを押すだけ。", None),
    ("h", "照合も、按分の計算も、会計用のCSVづくりも、自動で終わります。", None),
    ("i", "人が見るのは、システムが判断できなかったところだけ。", None),
    ("i", "そこを直せば、すぐに集計し直します。", None),
    ("o0", "ここからは、実際の画面で、操作の流れを見てみましょう。", None),
]


def add_steps() -> None:
    boxes = json.loads((DEMO / "boxes.json").read_text(encoding="utf-8"))
    prev = None
    for i, (name, title, label, text) in enumerate(STEPS, start=1):
        img = DEMO / f"{name}_plain.png"
        if not img.exists():
            raise SystemExit(f"{img} がありません。先に capture_demo.py を実行してください")
        box = boxes.get(name)
        SCRIPT.append(("scr", text, {"img": img.as_uri(), "box": box, "prev": prev, "label": label,
                                     "title": title, "step": i, "n": len(STEPS), "first": i == 1}))
        prev = box or prev
    SCRIPT.append(("k", "手作業の時間がほとんどなくなり、担当者の仕事は、確認だけになります。", None))
    SCRIPT.append(("k", "ガソリンカードの費用振替を、正確に、速く。", None))


def make_voices() -> list[Path]:
    WORK.mkdir(parents=True, exist_ok=True)
    items = [{"text": reading(t), "path": str(WORK / f"v{i:02d}.wav")} for i, (_, t, _) in enumerate(SCRIPT)]
    jf = WORK / "voices.json"
    jf.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    ps = f"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.SelectVoice('Microsoft Haruka Desktop'); $s.Rate = 1
$items = Get-Content -Raw -Encoding UTF8 '{jf}' | ConvertFrom-Json
foreach ($it in $items) {{ $s.SetOutputToWaveFile($it.path); $s.Speak($it.text) }}
$s.SetOutputToNull()
"""
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
    return [Path(x["path"]) for x in items]


def timeline(voices: list[Path]):
    """区間（=1つの描画単位）と字幕の時刻を決め、音声を1本につなぐ。操作画面は1文=1区間"""
    params, chunks, t = None, [], 0.0
    lines, segs = [], []
    prev_key = None
    for i, ((scene, text, prm), v) in enumerate(zip(SCRIPT, voices)):
        with wave.open(str(v)) as w:
            params = params or w.getparams()
            data = w.readframes(w.getnframes())
            dur = w.getnframes() / w.getframerate()
        key = f"{scene}#{i}" if prm else scene
        if key != prev_key:
            segs.append({"id": scene, "key": key, "start": t, "params": prm})
            lead = LEAD_SCR if prm else LEAD
            chunks.append(("sil", lead)); t += lead
        lines.append((key, t, t + dur + GAP, text))
        chunks.append(("pcm", data)); chunks.append(("sil", GAP)); t += dur + GAP
        prev_key = key
    chunks.append(("sil", TAIL)); t += TAIL
    total = t
    for i, s in enumerate(segs):
        s["end"] = segs[i + 1]["start"] if i + 1 < len(segs) else total
        s["cues"] = [ln[1] - s["start"] for ln in lines if ln[0] == s["key"]]
    audio = WORK / "narration.wav"
    with wave.open(str(audio), "wb") as w:
        w.setparams(params)
        bpf = params.sampwidth * params.nchannels
        for kind, val in chunks:
            w.writeframes(val if kind == "pcm" else b"\x00" * (int(val * params.framerate) * bpf))
    return lines, segs, total, audio


def draw(pg, seg, t_abs, lines, total) -> None:
    cap = next((ln[3] for ln in lines if ln[1] <= t_abs < ln[2]), "")
    if not cap:
        past = [ln for ln in lines if ln[1] <= t_abs]
        cap = past[-1][3] if past and past[-1][0] == seg["key"] else ""
    pg.evaluate("(a) => render(...a)", [seg["id"], t_abs - seg["start"], seg["cues"], cap, t_abs / total, seg["params"]])
    if seg["params"]:
        pg.wait_for_function("document.getElementById('scrImg').complete")


def render_frames(lines, segs, total) -> None:
    FRAMES.mkdir(parents=True, exist_ok=True)
    for f in FRAMES.glob("*.jpg"):
        f.unlink()
    n = int(total * FPS)
    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge")
        pg = b.new_page(viewport={"width": 1920, "height": 1080})
        pg.goto((HERE / "anim.html").as_uri(), wait_until="networkidle")
        pg.wait_for_timeout(800)
        for i in range(n):
            t = i / FPS
            seg = next(s for s in segs if s["start"] <= t < s["end"] or s is segs[-1])
            draw(pg, seg, t, lines, total)
            pg.screenshot(path=str(FRAMES / f"f{i:05d}.jpg"), type="jpeg", quality=90)
            if i % 300 == 0:
                print(f"  frame {i}/{n}", flush=True)
        b.close()


def build() -> None:
    add_steps()
    voices = make_voices()
    lines, segs, total, audio = timeline(voices)
    print(f"長さ {total:.1f} 秒 / {len(segs)} 区間", flush=True)
    render_frames(lines, segs, total)
    ff("-framerate", str(FPS), "-i", str(FRAMES / "f%05d.jpg"), "-i", str(audio),
       "-vf", "scale=out_range=tv:out_color_matrix=bt709,format=yuv420p", "-color_range", "tv", "-colorspace", "bt709",
       "-c:v", "libx264", "-preset", "medium", "-crf", "19",
       "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", "-shortest", "-movflags", "+faststart", str(OUT))
    print(f"動画: {OUT}")


def preview() -> None:
    """各区間の終わり近くを1コマずつ描いて確認用PNGにする"""
    add_steps()
    voices = make_voices()
    lines, segs, total, _ = timeline(voices)
    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge")
        pg = b.new_page(viewport={"width": 1920, "height": 1080})
        pg.goto((HERE / "anim.html").as_uri(), wait_until="networkidle")
        for k, s in enumerate(segs):
            t = min(s["end"] - 0.1, s["start"] + max(2.6, (s["end"] - s["start"]) * 0.7))
            draw(pg, s, t, lines, total)
            pg.screenshot(path=str(WORK / f"preview_{k:02d}.png"))
        b.close()
    print(f"長さ {total:.1f} 秒 / {len(segs)} 区間")


if __name__ == "__main__":
    preview() if "--preview" in sys.argv else build()
