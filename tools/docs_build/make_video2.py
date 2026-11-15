"""操作・仕組み編の動画を作る（資料/操作・仕組み解説動画.mp4）

先に capture_demo.py でアプリの操作画面を撮影しておくこと。
使い方:  .venv\\Scripts\\python tools\\docs_build\\make_video2.py
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from playwright.sync_api import sync_playwright

from make_video import READ, ff, reading, wav_seconds  # noqa: F401  (同じフォルダ)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WORK = ROOT / "data" / "video_work" / "v2"
DEMO = ROOT / "data" / "video_work" / "demo"
OUT = ROOT / "資料" / "操作・仕組み解説動画.mp4"
GAP = 0.45

READ.update({"最大剰余法": "さいだいじょうよほう", "按分先": "あんぶんさき", "90秒": "きゅうじゅうびょう",
             "0件": "ぜろけん", "5件": "ごけん", "6件": "ろっけん", "197枚": "ひゃくきゅうじゅうななまい",
             "346万2048円": "さんびゃくよんじゅうろくまん、にせんよんじゅうはちえん", "1円": "いちえん",
             "10月": "じゅうがつ", "Streamlit": "ストリームリット"})

M = "m"   # 仕組みスライド
S = "shot"  # 操作画面
# (種類, シーンID or 画像名, ステップ, 見出しタグ, 見出し, 字幕)
SCRIPT = [
    (M, "t1", 0, "", "", "このシステムが中でどう動いているのか、そして実際の操作方法を解説します。"),
    (M, "m2", 0, "", "", "まず全体の流れです。入力フォルダに、明細のPDF、管理台帳、給与按分資料を置きます。"),
    (M, "m2", 1, "", "", "実行すると、台帳取込、PDF読取、照合、按分、出力の5つの処理が順番に動き、出力フォルダにCSVとExcelができます。"),
    (M, "m2", 2, "", "", "画面で直した内容は修正ファイルに保存され、実行するたびに、この5つの処理に当て直されます。"),
    (M, "m3", 0, "", "", "1つ目は台帳の取込です。台帳には、カードごとに有効開始日と有効終了日があります。"),
    (M, "m3", 1, "", "", "10月分を処理するときは、10月に有効な行が自動で選ばれます。カードの持ち主が変わっても、台帳に行を足すだけで対応できます。"),
    (M, "m4", 0, "", "", "2つ目はPDFの読取です。PDFから文字を取り出し、決まった形の行から、日付や金額を読み取ります。"),
    (M, "m4", 1, "", "", "形が崩れて読めない行だけを、AIに渡して読んでもらいます。"),
    (M, "m4", 2, "", "", "最後に、明細の合計がカード小計と合うか、全カードの合計が請求合計と合うかを検算します。"),
    (M, "m5", 0, "", "", "3つ目は照合です。カード番号の全角やハイフンを揃えてから、台帳と突き合わせます。"),
    (M, "m5", 1, "", "", "台帳にあればOK。台帳にない、期間外ならエラー。名義が違う場合は要確認として知らせます。"),
    (M, "m6", 0, "", "", "4つ目は按分です。例えば1万1円を3つの施設に分けると、金額に端数が出ます。"),
    (M, "m6", 1, "", "", "1円未満を切り捨てると、合計が1円足りなくなります。"),
    (M, "m6", 2, "", "", "そこで足りない1円を、端数が一番大きい施設に配ります。これで合計は必ず元の金額と一致します。"),
    (M, "m7", 0, "", "", "5つ目は出力です。施設ごとに金額を合計し、本部から各施設へ費用を付け替える、会計用の行を作ります。"),
    (M, "m7", 1, "", "", "施設の車の分と、エリアマネージャーなどから按分された分を合わせた金額になります。"),
    (M, "m8", 0, "", "", "次に、エラーの扱いです。エラーが見つかったカードは保留にして、CSVは出力しません。"),
    (M, "m8", 1, "", "", "要確認と情報は、出力は止めずに、目で確認するためのお知らせです。"),
    (M, "m9", 0, "", "", "想定外のことが起きたときは、安全な側に倒します。ファイルが足りないときは、処理を止めて理由を表示します。"),
    (M, "m9", 1, "", "", "AIが使えないときや、AIの読んだ金額が小計と合わないときは、AIの値は使わず、手入力に回します。"),
    (M, "m9", 2, "", "", "3つの合計が合わないときは、CSVを作りません。前に作った古いCSVも消して、間違って取り込むのを防ぎます。"),
    (M, "m10", 0, "", "", "直した内容は、元のファイルではなく修正ファイルに、誰がいつ直したかと一緒に記録します。"),
    (M, "m10", 1, "", "", "実行のたびに当て直すので、何度でも集計し直せて、取消もできます。"),
    (M, "m11", 0, "", "", "ここからは実際の操作です。起動は、ターミナルでこのコマンドを実行します。"),
    (M, "m11", 1, "", "", "ブラウザで、この画面を開きます。"),
    (S, "01_start", 0, "準備", "起動した画面", "起動すると、この画面が表示されます。"),
    (S, "02_month", 0, "準備", "対象月を入力", "まず左のメニューで、対象月を入力します。"),
    (S, "03_who", 0, "準備", "担当者名を入力", "担当者名を入れておくと、修正の記録に名前が残ります。"),
    (S, "04_upload", 0, "準備", "入力ファイルを置く", "入力ファイルを置くを開き、PDFと2つのExcelを選んで保存します。"),
    (S, "05_run", 0, "実行", "実行ボタンを押す", "準備ができたら、実行ボタンを押します。"),
    (S, "06_running", 0, "実行", "自動で処理中", "台帳の取込から按分まで、自動で処理が進みます。"),
    (S, "07_result", 0, "実行", "結果の表示", "結果です。カード197枚、請求合計346万2048円。エラーが6件見つかりました。"),
    (S, "08_list", 0, "エラー修正", "エラーの一覧", "一覧には、どのステップで、どのカードに、何の問題があるかが表示されます。"),
    (S, "09_fix_card", 0, "エラー修正", "台帳にないカード", "エラーを開きます。台帳にないカードなので、利用施設を選んで確定を押します。"),
    (S, "10_after_fix", 0, "エラー修正", "自動で再集計", "自動で集計し直され、エラーが5件に減りました。"),
    (S, "11_fix_card2", 0, "エラー修正", "同じように確定", "もう1枚の未登録カードも、同じように確定します。"),
    (S, "12_fix_expired", 0, "エラー修正", "有効期間外のカード", "有効期間が切れていたカードも、利用施設を指定します。"),
    (S, "13_fix_facility", 0, "エラー修正", "施設マスタにない施設", "施設マスタにない施設コードは、正しい施設を選び直します。"),
    (S, "14_ratio_ng", 0, "エラー修正", "按分割合が90%", "按分割合の合計が90パーセントのエラーです。合計が100パーセントになるまで、確定ボタンは押せません。"),
    (S, "15_fix_alloc", 0, "エラー修正", "按分資料にいない人", "按分資料に載っていない人は、按分先と割合を入れて確定します。"),
    (S, "16_all_clear", 0, "確認・出力", "エラー0件", "これでエラーは0件。緑のメッセージが出て、CSVを出力できる状態になりました。"),
    (S, "17_check", 0, "確認・出力", "整合性チェック", "整合性チェックで、請求合計、按分後の合計、CSVの合計が一致していることを確認します。"),
    (S, "18_facility", 0, "確認・出力", "施設別集計", "施設別集計では、施設ごとの直接利用分と、按分で受け取った分が見られます。"),
    (S, "19_output", 0, "確認・出力", "ダウンロード", "出力タブから、費用振替CSVと2つのExcelをダウンロードし、会計システムに取り込みます。"),
    (S, "20_history", 0, "確認・出力", "修正履歴", "修正履歴には、誰がいつ何を直したかが残ります。間違えたときは、取消で元に戻せます。"),
    (M, "e1", 0, "", "", "操作は、置く、押す、直す、出す、の4つだけ。以上、操作と仕組みの解説でした。"),
]


def make_voices() -> list[Path]:
    WORK.mkdir(parents=True, exist_ok=True)
    items = [{"text": reading(r[5]), "path": str(WORK / f"v{i:02d}.wav")} for i, r in enumerate(SCRIPT)]
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


def make_frames(durations: list[float]) -> list[Path]:
    total, elapsed, paths = sum(durations), 0.0, []
    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge")
        pg = b.new_page(viewport={"width": 1920, "height": 1080})
        pg.goto((HERE / "slides2.html").as_uri(), wait_until="networkidle")
        for i, ((kind, sid, step, tag, title, text), d) in enumerate(zip(SCRIPT, durations)):
            elapsed += d
            img = (DEMO / f"{sid}.png").as_uri() if kind == S else ""
            pg.evaluate("(a) => show(...a)", ["shot" if kind == S else sid, step, text, elapsed / total, img, tag, title])
            if kind == S:
                pg.wait_for_function("document.getElementById('shotImg').complete")
            pg.wait_for_timeout(150)
            out = WORK / f"f{i:02d}.png"
            pg.screenshot(path=str(out))
            paths.append(out)
        b.close()
    return paths


def build() -> None:
    missing = [r[1] for r in SCRIPT if r[0] == S and not (DEMO / f"{r[1]}.png").exists()]
    if missing:
        raise SystemExit(f"操作画面がありません: {missing}（先に capture_demo.py を実行）")
    voices = make_voices()
    durations = [wav_seconds(v) + GAP for v in voices]
    frames = make_frames(durations)
    segs, prev = [], None
    for i, (row, frame, voice, d) in enumerate(zip(SCRIPT, frames, voices, durations)):
        key = row[1] if row[0] == M else row[3]
        vf = "format=yuv420p" if key == prev else "fade=t=in:st=0:d=0.35:color=0xf4f6f9,format=yuv420p"
        seg = WORK / f"seg{i:02d}.mp4"
        ff("-loop", "1", "-framerate", "30", "-i", str(frame), "-i", str(voice), "-t", f"{d:.3f}", "-vf", vf,
           "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-r", "30",
           "-af", "apad", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", str(seg))
        segs.append(seg)
        prev = key
    lst = WORK / "list.txt"
    lst.write_text("".join(f"file '{s.as_posix()}'\n" for s in segs), encoding="utf-8")
    ff("-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(OUT))
    print(f"動画: {OUT}  長さ {sum(durations):.1f} 秒 / {len(SCRIPT)} カット")


if __name__ == "__main__":
    build()
