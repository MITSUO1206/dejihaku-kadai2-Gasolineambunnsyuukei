"""解説動画（MP4）を作る

1. ナレーション1文ごとに Windows の日本語音声（Haruka）で WAV を作る
2. slides.html を Edge で開き、文ごとのスライド状態＋字幕を PNG に撮る
3. ffmpeg で「画像＋音声」の区間を作ってつなぐ → 資料/システム解説動画.mp4

使い方:  .venv\\Scripts\\python tools\\docs_build\\make_video.py
"""
from __future__ import annotations

import json
import subprocess
import wave
from pathlib import Path

import imageio_ffmpeg
from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WORK = ROOT / "data" / "video_work"
OUT = ROOT / "資料" / "システム解説動画.mp4"
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

# (シーン, ステップ, 字幕)。読み上げで誤読しやすい語は READ で置き換える
SCRIPT = [
    (1, 0, "このシステムは、ガソリンカードの費用振替を自動化するツールです。"),
    (1, 0, "何のためのシステムなのか、3分ほどで解説します。"),
    (2, 0, "グループには多くの施設があり、施設の車はガソリンカードで給油しています。"),
    (2, 1, "カードは全部でおよそ200枚あります。"),
    (2, 2, "ところが請求書は、毎月、本部にまとめて1枚で届きます。"),
    (3, 0, "本部が全額を払うと、帳簿の上では、本部がガソリン代を使ったことになってしまいます。"),
    (3, 1, "そこで、実際に使った施設ごとに金額を振り分け直します。これを費用振替と呼びます。"),
    (4, 0, "これまでは、この作業を担当者が毎月、手作業で行っていました。"),
    (4, 1, "カード番号から利用施設を台帳で調べ、明細の金額を1枚ずつ書き写します。"),
    (4, 2, "複数の施設を担当するエリアマネージャーの分は、施設ごとに割り振ります。"),
    (4, 3, "最後に会計システム用のファイルを作ります。時間がかかり、転記ミスや計算ミスの心配もあります。"),
    (5, 0, "割り振りの例です。佐藤さんは3つの施設を担当していて、今月のガソリン代は3万円でした。"),
    (5, 1, "給与按分資料には、佐藤さんの給料を各施設が何パーセント負担しているかが書かれています。"),
    (5, 2, "同じ割合で、ガソリン代を1万5千円、9千円、6千円に分けます。"),
    (6, 0, "このシステムは、利用明細のPDF、管理台帳、給与按分資料の3つを読み込みます。"),
    (6, 1, "照合、集計、割り振りを自動で行い、"),
    (6, 2, "会計システムに取り込めるCSVと、確認用のExcelを作ります。"),
    (7, 0, "処理は6つのステップです。まず台帳を取り込み、PDFから金額を読み取ります。"),
    (7, 1, "次にカード番号で照合し、エリアマネージャーなどの分を割り振ります。"),
    (7, 2, "そしてCSVを作ります。見つかったエラーは一覧にし、直したら自動で集計し直します。"),
    (8, 0, "担当者がやるのは、システムが判断できなかったところを確認して直すことだけです。"),
    (8, 1, "たとえば、台帳に載っていないカードや、割合を足しても100パーセントにならないケースです。"),
    (8, 2, "画面で正しい値を入れると、自動で集計し直します。元のファイルは書き換えません。"),
    (9, 0, "お金を扱うので、安全装置があります。"),
    (9, 1, "請求書の合計、按分後の合計、CSVの合計。この3つが一致しないと、CSVは作られません。"),
    (10, 0, "PDFの文字が崩れて読めない行は、AIのClaudeが読み取りを手伝います。"),
    (10, 1, "送るのは読めなかった行の文字だけで、カード番号や名前は送りません。"),
    (10, 2, "読み取った金額は、カードの小計と一致したときだけ採用されます。"),
    (11, 0, "使い方は簡単です。ファイルを置いて、実行ボタンを押します。"),
    (11, 1, "エラーを直し、合計の一致を確認したら、CSVをダウンロードします。"),
    (12, 0, "手作業で数時間かかっていた作業が、ボタンひとつとエラーの確認だけになります。"),
    (12, 1, "ガソリンカードの費用振替を、正確に、速く。以上、システムの解説でした。"),
]
READ = {"給与按分資料": "きゅうよあんぶんしりょう", "按分": "あんぶん", "CSV": "シーエスブイ", "PDF": "ピーディーエフ",
        "Excel": "エクセル", "Claude": "クロード", "AI": "エーアイ", "200枚": "にひゃくまい", "1枚": "いちまい",
        "1万5千円": "いちまんごせんえん", "9千円": "きゅうせんえん", "6千円": "ろくせんえん", "3万円": "さんまんえん",
        "3分": "さんぷん", "帳簿": "ちょうぼ", "転記": "てんき"}
GAP = 0.45  # 文と文の間の無音（秒）


def reading(text: str) -> str:
    for k in sorted(READ, key=len, reverse=True):
        text = text.replace(k, READ[k])
    return text


def make_voices() -> list[Path]:
    WORK.mkdir(parents=True, exist_ok=True)
    items = [{"text": reading(t), "path": str(WORK / f"v{i:02d}.wav")} for i, (_, _, t) in enumerate(SCRIPT)]
    jf = WORK / "voices.json"
    jf.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    ps = f"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.SelectVoice('Microsoft Haruka Desktop')
$s.Rate = 1
$items = Get-Content -Raw -Encoding UTF8 '{jf}' | ConvertFrom-Json
foreach ($it in $items) {{ $s.SetOutputToWaveFile($it.path); $s.Speak($it.text) }}
$s.SetOutputToNull()
"""
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
    return [Path(x["path"]) for x in items]


def wav_seconds(p: Path) -> float:
    with wave.open(str(p)) as w:
        return w.getnframes() / w.getframerate()


def make_frames(durations: list[float]) -> list[Path]:
    total = sum(durations)
    paths, elapsed = [], 0.0
    with sync_playwright() as p:
        b = p.chromium.launch(channel="msedge")
        pg = b.new_page(viewport={"width": 1920, "height": 1080})
        pg.goto((HERE / "slides.html").as_uri(), wait_until="networkidle")
        for i, ((scene, step, text), d) in enumerate(zip(SCRIPT, durations)):
            elapsed += d
            pg.evaluate("([a,b,c,d]) => show(a,b,c,d)", [scene, step, text, elapsed / total])
            pg.wait_for_timeout(120)
            out = WORK / f"f{i:02d}.png"
            pg.screenshot(path=str(out))
            paths.append(out)
        b.close()
    return paths


def ff(*args: str) -> None:
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", *args], check=True)


def build() -> None:
    voices = make_voices()
    durations = [wav_seconds(v) + GAP for v in voices]
    frames = make_frames(durations)
    segs = []
    prev_scene = None
    for i, ((scene, _, _), frame, voice, d) in enumerate(zip(SCRIPT, frames, voices, durations)):
        seg = WORK / f"seg{i:02d}.mp4"
        vf = "format=yuv420p"
        if scene != prev_scene:  # シーンの切り替わりはフェードイン
            vf = "fade=t=in:st=0:d=0.35:color=0xf4f6f9," + vf
        ff("-loop", "1", "-framerate", "30", "-i", str(frame), "-i", str(voice),
           "-t", f"{d:.3f}", "-vf", vf, "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-r", "30",
           "-af", "apad", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2", str(seg))
        segs.append(seg)
        prev_scene = scene
    lst = WORK / "list.txt"
    lst.write_text("".join(f"file '{s.as_posix()}'\n" for s in segs), encoding="utf-8")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    ff("-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(OUT))
    print(f"動画: {OUT}  長さ {sum(durations):.1f} 秒 / {len(SCRIPT)} カット")


if __name__ == "__main__":
    build()
