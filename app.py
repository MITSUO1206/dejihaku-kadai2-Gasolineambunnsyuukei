"""操作画面（Streamlit）

起動:  .venv\\Scripts\\streamlit run app.py

画面:
  ログイン（初回は管理者アカウントの作成）
  集計        … 管理者・担当者：ファイルを置く → 実行 → エラー修正 → CSV出力
  月次推移    … 全員：CSVを出力できた月の施設別金額をSQLで集計
  管理        … 管理者：ユーザー管理・設定・操作ログ
"""
from __future__ import annotations

import os
import re
import secrets as _secrets
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

from src import auth, db, demo, reports, settings
from src.common import ROOT, ai_key_configured, format_card_no, load_config, month_label
from src.errors import ERROR, INFO, WARNING
from src.exporter import summary_sheets
from src.pipeline import corrections_path, load_corrections, run, save_correction

st.set_page_config(page_title="ガソリンカード費用振替", page_icon="⛽", layout="wide")
APP_NAME = "ガソリンカード費用振替 自動化システム"
SESSION_TIMEOUT = timedelta(minutes=30)
PAGES = {"集計": {"admin", "staff"}, "月次推移": {"admin", "staff", "viewer"}, "管理": {"admin"}}
cfg = load_config()
if demo.enabled():
    demo.seed(cfg)
    st.info("🧪 公開デモです。表示されているデータはすべて架空です。実際のデータはアップロードしないでください。", icon="ℹ️")


# =============================================================================
# ログイン・セッション
# =============================================================================
def logout(reason: str = "") -> None:
    u = st.session_state.get("user")
    if u:
        db.log(u["username"], "ログアウト", reason)
    for k in list(st.session_state.keys()):
        del st.session_state[k]


def screen_setup() -> None:
    st.title(f"⛽ {APP_NAME}")
    st.subheader("はじめに：管理者アカウントを作成")
    st.info("ユーザーがまだ登録されていません。最初の管理者を作成してください。"
            "この画面は管理者が1人登録されると表示されなくなります。")
    # 公開環境では、設定した「初期設定キー」を知っている人だけが最初の管理者を作れる
    setup_token = os.environ.get("GASCARD_SETUP_TOKEN", "")
    with st.form("setup"):
        token = st.text_input("初期設定キー", type="password") if setup_token else ""
        username = st.text_input("ユーザー名（半角英数字）")
        name = st.text_input("表示名（例：経理 山田）")
        pw1 = st.text_input("パスワード（8文字以上・英字と数字を含む）", type="password")
        pw2 = st.text_input("パスワード（確認）", type="password")
        if st.form_submit_button("管理者を作成", type="primary"):
            if setup_token and not _secrets.compare_digest(token, setup_token):
                db.log(username or "-", "初期設定失敗", "初期設定キー違い")
                st.error("初期設定キーが違います")
            elif pw1 != pw2:
                st.error("パスワードが一致しません")
            else:
                try:
                    auth.create_first_admin(username, name, pw1)
                    st.success("作成しました。ログインしてください。")
                    st.rerun()
                except auth.AuthError as e:
                    st.error(str(e))


def screen_login() -> None:
    st.title(f"⛽ {APP_NAME}")
    if st.session_state.get("logout_msg"):
        st.warning(st.session_state.pop("logout_msg"))
    _, mid, _ = st.columns([1, 1.2, 1])
    with mid, st.form("login"):
        st.subheader("ログイン")
        username = st.text_input("ユーザー名")
        pw = st.text_input("パスワード", type="password")
        if st.form_submit_button("ログイン", type="primary", width="stretch"):
            try:
                st.session_state.user = auth.authenticate(username, pw)
                st.session_state.last_active = datetime.now()
                st.rerun()
            except auth.AuthError as e:
                st.error(str(e))
        st.caption("パスワードを忘れた場合は、管理者に再発行を依頼してください。")
    if demo.enabled():
        with mid:
            st.markdown("**デモ用アカウント**（パスワードはすべて `" + demo.PASSWORD + "`）")
            st.table(pd.DataFrame([{"ユーザー名": u, "役割": auth.ROLES[r]} for u, _, r in demo.USERS]))


def form_change_password(user: dict, forced: bool) -> None:
    if forced:
        st.title("パスワードの変更")
        st.warning("仮パスワードでログインしています。続けるには新しいパスワードを設定してください。")
    with st.form("chpw"):
        cur = st.text_input("現在のパスワード", type="password")
        new1 = st.text_input("新しいパスワード（8文字以上・英字と数字を含む）", type="password")
        new2 = st.text_input("新しいパスワード（確認）", type="password")
        if st.form_submit_button("変更する", type="primary"):
            if new1 != new2:
                st.error("新しいパスワードが一致しません")
            else:
                try:
                    auth.change_password(user["username"], cur, new1)
                    st.success("パスワードを変更しました")
                    st.rerun()
                except auth.AuthError as e:
                    st.error(str(e))


def require_login() -> dict:
    """ログイン済みのユーザーを返す。未ログインならログイン画面を出して止める"""
    if auth.user_count() == 0:
        screen_setup(); st.stop()
    u = st.session_state.get("user")
    if not u:
        screen_login(); st.stop()
    last = st.session_state.get("last_active", datetime.now())
    if datetime.now() - last > SESSION_TIMEOUT:
        logout("30分操作がなかったため")
        st.session_state.logout_msg = "30分操作がなかったため、ログアウトしました。もう一度ログインしてください。"
        st.rerun()
    st.session_state.last_active = datetime.now()
    fresh = auth.get_user(u["username"])  # 無効化・役割変更をすぐ反映する
    if not fresh or not fresh["active"]:
        logout("無効化された")
        st.session_state.logout_msg = "このユーザーは無効になりました。管理者に連絡してください。"
        st.rerun()
    st.session_state.user = fresh
    if fresh["must_change"]:
        form_change_password(fresh, forced=True); st.stop()
    return fresh


# =============================================================================
# 集計ページ（管理者・担当者）：① ファイルを置く → ② 実行 → ③ エラーを直す → ④ CSVを出す
# =============================================================================
UPLOAD_KINDS = {  # 種類: (名前, 拡張子, ファイル名の頭, 中身の先頭バイト, 説明)
    "pdf": ("利用明細（PDF）", "pdf", "利用明細_", b"%PDF-", "カード会社から届いた、今月のご利用代金明細書"),
    "ledger": ("管理台帳（Excel）", "xlsx", "管理台帳_", b"PK", "どのカードを、どの施設・誰が使っているかの一覧"),
    "alloc": ("給与按分資料（Excel）", "xlsx", "給与按分_", b"PK", "AM・アドバイザーの費用を、どの施設に何%分けるか"),
}
STEPS = ["ファイルを置く", "集計を実行する", "エラーを直す", "CSVを出す"]

# エラーを、経理担当者に伝わる言葉で（タイトル, 画面でやること）
PLAIN = {
    "L001": ("管理台帳に空欄があります", "このカードを使った施設を選んでください。"),
    "L002": ("同じカードが管理台帳に2行あります", "管理台帳の有効期間を直して、ファイルを置き直してください。"),
    "L003": ("管理台帳の施設コードが、施設一覧にありません", "このカードを使った施設を選び直してください。"),
    "L004": ("管理台帳の「区分」が正しくありません", "このカードの区分と施設を選んでください。"),
    "P001": ("明細PDFの金額が読み取れませんでした", "PDFの原本を見て、このカードの今月の利用金額を入力してください。"),
    "P002": ("明細の合計と、カードの小計が合いません", "PDFの原本を見て、このカードの今月の利用金額を入力してください。"),
    "P003": ("請求合計と、読み取った金額の合計が合いません", "明細PDFが正しいファイルか確認してください。直らない場合は管理者に連絡してください。"),
    "P004": ("明細PDFに「ご請求合計金額」が見つかりません", "明細PDFが正しいファイルか確認してください。"),
    "M001": ("管理台帳に登録されていないカードが使われています", "このカードを使った施設を選んでください。来月のために、管理台帳にも登録してください。"),
    "M002": ("有効期間が切れたカードが使われています", "このカードを使った施設を選んでください。管理台帳の有効期間も確認してください。"),
    "M003": ("カードの名義が、管理台帳と明細で違います", "管理台帳の名義が古くないか確認してください。集計はこのまま進められます。"),
    "M004": ("AM・アドバイザーなのに社員番号がありません", "このカードの区分・施設・社員番号を入力してください。"),
    "A001": ("按分の割合が登録されていない人がいます", "この人のガソリン代を、どの施設に何%ずつ分けるか入力してください（合計100%）。"),
    "A002": ("按分の割合を足しても100%になりません", "割合を直して、合計を100%にしてください。"),
    "A003": ("按分先の施設コードが、施設一覧にありません", "按分先の施設を選び直してください。"),
    "P005": ("AIが読み取った金額があります", "念のため、PDFの原本と金額が合っているか見てください。集計はこのまま進められます。"),
    "M005": ("今月使われなかったカードがあります", "対応は不要です（使っていないカードの確認用）。"),
    "C001": ("合計が合いません", "システムの不具合の可能性があります。管理者に連絡してください。"),
}
CARD_FIX = ("M001", "M002", "L001", "L003", "L004", "M004")
AMOUNT_FIX = ("P001", "P002")
ALLOC_FIX = ("A001", "A002", "A003")


def workspace_cfg() -> dict:
    """公開デモでは、人（セッション）ごとに専用の作業フォルダを使う。他の人のファイルや修正と混ざらない"""
    if not demo.enabled():
        return cfg
    import copy
    import shutil
    sid = st.session_state.setdefault("sid", _secrets.token_hex(8))
    base = ROOT / "data" / "demo_sessions"
    if not st.session_state.get("demo_cleaned"):  # 1日以上前の作業フォルダを消す
        cutoff = datetime.now().timestamp() - 86400
        for d in base.glob("*") if base.exists() else []:
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
        st.session_state.demo_cleaned = True
    w = copy.deepcopy(cfg)
    for key in ("input_dir", "output_dir", "corrections_dir"):  # ログ（AIの再利用・回数上限）は全員で共有
        w["paths"][key] = str(base / sid / key)
    for month, folder in demo.SAMPLES.items():
        dst = Path(w["paths"]["input_dir"]) / month
        if not dst.exists():
            shutil.copytree(ROOT / "入力ファイル見本" / folder, dst)
    return w


def stepper(current: int, done: set[int]) -> None:
    cols = st.columns(len(STEPS))
    for i, (col, name) in enumerate(zip(cols, STEPS), start=1):
        if i in done:
            col.success(f"✓ {i}. {name}")
        elif i == current:
            col.info(f"▶ {i}. {name}")
        else:
            col.container(border=True).caption(f"{i}. {name}")


def page_aggregate(user: dict) -> None:
    who = f"{user['display_name']}（{user['username']}）"
    cfg = workspace_cfg()
    input_root = ROOT / cfg["paths"]["input_dir"]

    # ---- 対象月（選ぶだけ。新しい月は追加ボタンから） ----
    st.title("毎月の集計")
    months = sorted([p.name for p in input_root.iterdir() if p.is_dir()], reverse=True) if input_root.exists() else []
    extra = st.session_state.get("new_months", [])
    months = sorted(set(months) | set(extra), reverse=True) or [datetime.now().strftime("%Y-%m")]
    if "_pending_month" in st.session_state:  # 「別の月を追加」で選んだ月を、選択欄を作る前に反映する
        st.session_state.month = st.session_state.pop("_pending_month")
    c1, c2 = st.columns([2, 3])
    month = c1.selectbox("対象月（カードを使った月）", months, format_func=month_label, key="month")
    with c2.popover("＋ 別の月を追加"):
        nm = st.text_input("追加する月（例：2026-12）", key="new_month_input").strip()
        if st.button("この月を追加する"):
            if re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", nm):
                st.session_state.new_months = sorted(set(extra) | {nm})
                st.session_state._pending_month = nm
                st.rerun()
            else:
                st.error("2026-12 のように「年4けた-月2けた」で入力してください")

    in_dir = input_root / month
    present = {k: sorted(in_dir.glob(f"{v[2]}*.{v[1]}")) for k, v in UPLOAD_KINDS.items()} if in_dir.exists() else \
        {k: [] for k in UPLOAD_KINDS}
    files_ok = all(present.values())
    res = st.session_state.get("res")
    if res is not None and res.month != month:
        res = None

    def execute(use_ai: bool) -> None:
        with st.spinner("照合・集計・按分をしています…（30秒ほどかかることがあります）"):
            try:
                st.session_state.res = run(month, cfg, use_ai=use_ai, user=user["username"])
                st.session_state.run_error = None
            except Exception as e:  # 入力ファイル不足・列名違いなど
                st.session_state.res = None
                st.session_state.run_error = str(e)
                db.log(user["username"], "集計失敗", f"{month} {e}")

    has_key = ai_key_configured()
    use_ai = st.session_state.get("use_ai", bool(cfg["ai"]["enabled"] and has_key))

    def rerun_after_save(msg: str) -> None:
        execute(use_ai)
        st.toast(msg + " → 集計し直しました")
        st.rerun()

    n_err = res.error_count if res else 0
    current = 1 if not files_ok else 2 if res is None else 3 if n_err else 4
    done = {i for i in range(1, current)}
    stepper(current, done)
    st.write("")

    # ---- ① ファイルを置く ----
    with st.container(border=True):
        if files_ok:  # 終わった手順は1行に縮めて、今の手順を画面の上に出す
            st.markdown("**✓ 1. ファイル**　3つそろっています（" + "、".join(present[k][-1].name for k in UPLOAD_KINDS) + "）")
        else:
            st.subheader("1. ファイルを置く")
            for k, v in UPLOAD_KINDS.items():
                if present[k]:
                    st.markdown(f"✅ **{v[0]}**：{present[k][-1].name}")
                else:
                    st.markdown(f"⬜ **{v[0]}**：まだありません　<span style='color:gray'>— {v[4]}</span>",
                                unsafe_allow_html=True)
        with st.expander("ファイルを置き直す" if files_ok else "ファイルを置く", expanded=not files_ok):
            if demo.enabled():
                st.warning("公開デモです。実際の明細や個人情報を含むファイルは置かないでください。"
                           "置いたファイルはあなた専用の場所に保存され、1日で自動的に消えます。")
                with st.popover("試せるサンプルファイルをダウンロード"):
                    for sm, folder in demo.SAMPLES.items():
                        st.caption(f"{month_label(sm)}分（{'エラー入り' if sm == '2026-10' else 'エラーなし'}）")
                        for f in sorted((ROOT / "入力ファイル見本" / folder).iterdir()):
                            st.download_button(f"⬇ {f.name}", f.read_bytes(), file_name=f.name, key=f"smp{f.name}")
            st.caption("ファイルをここにドラッグするか、「Upload」を押して選びます。選んだあと「保存する」を押すまでは置かれません。")
            ups = {k: st.file_uploader(f"{v[0]} — {v[4]}", type=v[1], key=f"up_{k}") for k, v in UPLOAD_KINDS.items()}
            if st.button("選んだファイルを保存する", disabled=not any(ups.values()), type="primary" if not files_ok else "secondary"):
                problems = []
                for k, f in ups.items():
                    if f and not f.getvalue().startswith(UPLOAD_KINDS[k][3]):
                        problems.append(f"「{f.name}」は{UPLOAD_KINDS[k][0]}のファイルではありません")
                if problems:
                    st.error("保存しませんでした：" + "／".join(problems))
                else:
                    in_dir.mkdir(parents=True, exist_ok=True)
                    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    saved = []
                    for k, f in ups.items():
                        if not f:
                            continue
                        _, ext, prefix, _, _ = UPLOAD_KINDS[k]
                        for old in in_dir.glob(f"{prefix}*.{ext}"):  # 古い方は「置き換え前」へ（読み違い防止）
                            bk = in_dir / "置き換え前" / stamp
                            bk.mkdir(parents=True, exist_ok=True)
                            old.rename(bk / old.name)
                        name = Path(f.name).name
                        name = name if name.startswith(prefix) else prefix + name
                        (in_dir / name).write_bytes(f.getvalue())
                        saved.append(name)
                    db.log(user["username"], "ファイル保存", f"{month} {', '.join(saved)}")
                    st.session_state.res = None  # ファイルが変わったので、集計はやり直し
                    st.toast("ファイルを保存しました。次は「集計を実行する」を押してください。")
                    st.rerun()

    # ---- ② 集計を実行する ----
    with st.container(border=True):
        if res is not None:
            st.markdown(f"**✓ 2. 集計**　カード {len(res.cards)}枚／請求合計 ¥{res.totals['PDF請求合計']:,}")
        else:
            st.subheader("2. 集計を実行する")
        if not files_ok:
            missing = [UPLOAD_KINDS[k][0] for k, v in present.items() if not v]
            st.caption("先に、1. でファイルを3つそろえてください（足りないもの：" + "、".join(missing) + "）")
        if res is None:
            st.caption("明細と台帳を照らし合わせ、施設ごとに金額を分けます。この時点ではまだCSVは作られません。")
        if st.button("▶ 集計を実行する" if res is None else "↻ もう一度集計する",
                     type="primary" if current == 2 else "secondary", disabled=not files_ok):
            execute(use_ai)
            st.rerun()
        with st.expander("詳しい設定"):
            st.toggle("読めない行をAIで読み取る（AI読取補助）", value=use_ai, disabled=not has_key, key="use_ai",
                      help="明細PDFで読めなかった行だけをAIに送ります。カード番号・名前は送りません。")
            if not has_key:
                st.caption("AIのキーが設定されていないため使えません。読めない行は手入力になります。")
            elif demo.enabled():
                from src.ai_assist import daily_limit
                st.caption(f"デモでは、同じ行は前回の結果を使い、AIへの問い合わせは1日{daily_limit()}回までです。")
        if st.session_state.get("run_error"):
            st.error(f"集計できませんでした：{st.session_state.run_error}\n\n"
                     "ファイルの種類や、Excelの列名が正しいか確認してください。")

    if res is None:
        return

    issues = res.issues
    errors = [i for i in issues if i.severity == ERROR]
    warns = [i for i in issues if i.severity == WARNING]
    fac = res.ledger.facilities
    fac_codes = list(fac["facility_code"])
    fac_label = dict(zip(fac["facility_code"], fac["facility_name"]))

    # ---- ③ エラーを直す ----
    with st.container(border=True):
        st.subheader("3. エラーを直す")
        if not errors:
            st.success("直す必要があるエラーはありません。")
        else:
            held = res.totals["エラーで保留中の金額"]
            st.warning(f"直す必要があるエラーが **{len(errors)}件** あります（この分の ¥{held:,} はまだCSVに入っていません）。"
                       "上から順に直してください。「確定」を押すと、自動で集計し直します。")
        seen_emp: set[str] = set()
        for n, iss in enumerate(errors):
            if iss.code in ALLOC_FIX:
                emp = iss.ref.get("employee_no", "")
                if emp in seen_emp:
                    continue
                seen_emp.add(emp)
            title, todo = PLAIN.get(iss.code, (iss.title, iss.action))
            with st.container(border=True):
                st.markdown(f"**{n + 1}. {title}**")
                facts = []
                if iss.card_no:
                    facts.append(f"カード {format_card_no(iss.card_no)}")
                if iss.user:
                    facts.append(f"利用者・名義 {iss.user}")
                if iss.amount is not None:
                    facts.append(f"金額 ¥{iss.amount:,}")
                st.caption("　／　".join(facts + [f"詳しく：{iss.detail}", f"コード {iss.code}"]))
                st.markdown(f"👉 {todo}")

                if iss.code in AMOUNT_FIX:
                    default = iss.ref.get("subtotal") or iss.ref.get("read_sum") or 0
                    amt = st.number_input("このカードの今月の利用金額（円）", value=int(default), step=1, min_value=0,
                                          key=f"amt{n}", help="明細PDFの「カード小計」の金額を入れてください")
                    if st.button("この金額で確定", key=f"b{n}", type="primary"):
                        save_correction(cfg, month, "pdf_overrides", iss.card_no, {"amount": int(amt)}, who,
                                        f"{iss.code}: {iss.detail}")
                        rerun_after_save("金額を確定しました")

                elif iss.code in CARD_FIX:
                    a, b_, c = st.columns(3)
                    f_sel = a.selectbox("使った施設", [None] + fac_codes, key=f"f{n}",
                                        format_func=lambda x: "選んでください" if x is None else f"{x} {fac_label[x]}")
                    cat = b_.selectbox("区分", ["施設", "AM", "アドバイザー"], key=f"c{n}",
                                       help="施設の車なら「施設」。複数施設を担当する人のカードなら「AM」「アドバイザー」")
                    emp = c.text_input("社員番号（AM・アドバイザーのとき）", key=f"e{n}")
                    need_emp = cat != "施設" and not emp.strip()
                    if need_emp:
                        st.caption("AM・アドバイザーの場合は社員番号を入れてください。")
                    if st.button("この内容で確定", key=f"b{n}", type="primary", disabled=f_sel is None or need_emp):
                        save_correction(cfg, month, "card_overrides", iss.card_no, {
                            "facility_code": f_sel, "category": cat, "employee_no": emp.strip(),
                            "user_name": iss.user, "card_holder": iss.ref.get("holder", "")}, who,
                            f"{iss.code}: {iss.detail}")
                        rerun_after_save("カードの施設を確定しました")

                elif iss.code in ALLOC_FIX:
                    emp = iss.ref.get("employee_no", "")
                    plan = pd.DataFrame(iss.ref.get("plan") or [{"facility_code": None, "ratio": 100.0}])
                    st.caption("行を追加・削除できます。施設を選び、割合（%）を入れてください。")
                    edited = st.data_editor(
                        plan, num_rows="dynamic", key=f"pl{n}", width="stretch",
                        column_config={
                            "facility_code": st.column_config.SelectboxColumn(
                                "按分先の施設", options=fac_codes, required=True),
                            "ratio": st.column_config.NumberColumn("割合（%）", min_value=0, max_value=100, required=True)})
                    total = float(edited["ratio"].fillna(0).sum())
                    ok = abs(total - 100) < 1e-6 and edited["facility_code"].notna().all() and len(edited) > 0
                    (st.success if ok else st.info)(f"いまの合計：{total:g}%" + ("" if ok else "　（100%にすると確定できます）"))
                    if st.button("この割合で確定", key=f"b{n}", type="primary", disabled=not ok):
                        save_correction(cfg, month, "alloc_overrides", emp, edited.dropna().to_dict("records"), who,
                                        f"{iss.code}: {iss.detail}")
                        rerun_after_save("按分の割合を確定しました")
                else:
                    st.caption("この画面では直せないエラーです。")

        if warns:
            with st.expander(f"念のため確認してほしいもの（{len(warns)}件）— 直さなくてもCSVは出せます"):
                for iss in warns:
                    title, todo = PLAIN.get(iss.code, (iss.title, iss.action))
                    st.markdown(f"**{title}**　{todo}")
                    st.caption(f"{format_card_no(iss.card_no) if iss.card_no else ''}　{iss.detail}")

        corr = load_corrections(cfg, month)
        fixes = [(k, key, val) for k in ("pdf_overrides", "card_overrides", "alloc_overrides")
                 for key, val in corr[k].items()]
        if fixes:
            with st.expander(f"これまでに直した内容（{len(fixes)}件）— 間違えたときはここで取り消せます"):
                kind_label = {"pdf_overrides": "金額", "card_overrides": "カードの施設", "alloc_overrides": "按分の割合"}
                for kind, key, val in fixes:
                    a, b_ = st.columns([5, 1])
                    if kind == "pdf_overrides":
                        desc = f"カード {format_card_no(key)} の金額 → ¥{val['amount']:,}"
                    elif kind == "card_overrides":
                        desc = f"カード {format_card_no(key)} → {val['facility_code']} {fac_label.get(val['facility_code'], '')}（{val['category']}）"
                    else:
                        desc = f"社員番号 {key} → " + "、".join(f"{p['facility_code']} {p['ratio']:g}%" for p in val)
                    a.markdown(f"{kind_label[kind]}：{desc}")
                    if b_.button("取り消す", key=f"del{kind}{key}"):
                        save_correction(cfg, month, kind, key, None, who, "取消")
                        rerun_after_save("取り消しました")

    # ---- ④ CSVを出す ----
    with st.container(border=True):
        st.subheader("4. CSVを出す")
        if res.can_export:
            t = res.totals
            st.success(f"エラーはありません。請求合計 ¥{t['PDF請求合計']:,} ＝ 施設に分けた合計 ¥{t['按分後合計']:,} ＝ "
                       f"CSVの合計 ¥{t['CSV借方合計']:,} で、すべて一致しています。")
            p = res.outputs.get("csv")
            if p and p.exists():
                st.download_button("⬇ 費用振替CSVをダウンロード（会計システムに取り込む用）", p.read_bytes(),
                                   file_name=p.name, type="primary", key="dlcsv",
                                   on_click=db.log, args=(user["username"], "ダウンロード", f"{month} {p.name}"))
            a, b_ = st.columns(2)
            for col, key, label in [(a, "summary", "⬇ 集計一覧（Excel・保管用）"), (b_, "errors", "⬇ エラー一覧（Excel・記録用）")]:
                p = res.outputs.get(key)
                if p and p.exists():
                    col.download_button(label, p.read_bytes(), file_name=p.name, key=f"dl{key}",
                                        on_click=db.log, args=(user["username"], "ダウンロード", f"{month} {p.name}"))
            with st.expander("CSVの中身を見る"):
                st.dataframe(res.journal, width="stretch", hide_index=True)
        else:
            st.info(f"あと **{n_err}件** のエラーを直すと、ここに「CSVをダウンロード」ボタンが出ます。"
                    "（間違った金額が会計に入らないよう、エラーが残っている間はCSVを作りません）")

    # ---- 詳しく見る ----
    with st.expander("詳しく見る（カード別・施設別・明細・合計の内訳）"):
        sheets = summary_sheets(res)
        t1, t2, t3, t4 = st.tabs(["施設別", "カード別", "明細", "合計の内訳"])
        t1.dataframe(sheets["施設別集計"], width="stretch", hide_index=True)
        t2.dataframe(sheets["カード別一覧"], width="stretch", hide_index=True)
        t3.dataframe(sheets["利用明細"], width="stretch", hide_index=True)
        t4.table(pd.DataFrame([{"項目": k, "金額": f"¥{v:,}"} for k, v in res.totals.items()]))


# =============================================================================
# 月次推移ページ（全員）
# =============================================================================
def page_trend() -> None:
    st.title("月次推移")
    st.caption("CSVを出力できた（エラー0件・合計一致の）月の、施設別の確定金額を集計しています。")
    total = reports.monthly_total()
    if total.empty:
        st.info("まだデータがありません。集計ページでエラーを0件にしてCSVを出力すると、ここに月ごとの結果がたまります。")
        return

    st.subheader("月ごとの合計")
    st.bar_chart(total.set_index("対象月")[["直接利用", "按分受入"]], stack=True)
    st.dataframe(total.style.format({c: "{:,}" for c in ["直接利用", "按分受入", "合計"]}),
                 width="stretch", hide_index=True)

    st.subheader("施設別ランキング")
    m = st.selectbox("対象月", reports.months())
    rk = reports.ranking(m)
    st.dataframe(rk.style.format({"直接利用": "{:,}", "按分受入": "{:,}", "合計": "{:,}", "構成比": "{:.1f}%"}),
                 width="stretch", hide_index=True)

    st.subheader("施設別の推移")
    tr = reports.facility_trend()
    names = sorted(tr["施設名"].unique())
    pick = st.multiselect("施設を選ぶ", names, default=list(rk["施設名"].head(5)))
    if pick:
        pv = tr[tr["施設名"].isin(pick)].pivot(index="対象月", columns="施設名", values="金額")
        st.line_chart(pv)
        st.dataframe(tr[tr["施設名"].isin(pick)].style.format({"金額": "{:,}", "前月差": "{:+,.0f}"}, na_rep="—"),
                     width="stretch", hide_index=True)

    with st.expander("実行の記録"):
        st.dataframe(reports.runs(), width="stretch", hide_index=True)
    with st.expander("この画面で使っているSQL"):
        st.code(reports.SQL_MONTHLY_TOTAL.strip(), language="sql")
        st.code(reports.SQL_RANKING.strip(), language="sql")
        st.code(reports.SQL_FACILITY_TREND.strip(), language="sql")


# =============================================================================
# 管理ページ（管理者）
# =============================================================================
def page_admin(me: dict) -> None:
    st.title("管理")
    t_users, t_settings, t_logs = st.tabs(["👤 ユーザー管理", "⚙ 設定", "📜 操作ログ"])

    with t_users:
        if "issued_pw" in st.session_state:
            u, pw = st.session_state.pop("issued_pw")
            st.success(f"**{u}** の仮パスワード： `{pw}`\n\n"
                       "この画面を閉じると二度と表示されません。本人に安全な方法で伝えてください。"
                       "初回ログイン時にパスワードの変更を求められます。")
        users = auth.list_users()
        view = pd.DataFrame([{
            "ユーザー名": u["username"], "表示名": u["display_name"], "役割": u["role_label"],
            "状態": "有効" if u["active"] else "無効",
            "ロック": "ロック中" if u["locked_until"] and u["locked_until"] > db.now() else "",
            "最終ログイン": u["last_login_at"] or "—"} for u in users])
        st.dataframe(view, width="stretch", hide_index=True)

        c1, c2 = st.columns(2)
        with c1, st.form("add_user", clear_on_submit=True):
            st.markdown("**ユーザーを追加**")
            un = st.text_input("ユーザー名（半角英数字）")
            dn = st.text_input("表示名")
            rl = st.selectbox("役割", list(auth.ROLES), format_func=lambda r: auth.ROLES[r], index=1)
            st.caption("担当者：集計・修正・CSV出力 ／ 閲覧者：月次推移を見るだけ ／ 管理者：すべて＋この画面")
            if st.form_submit_button("追加する（仮パスワードを発行）", type="primary"):
                pw = auth.temp_password()
                try:
                    auth.create_user(un, dn, rl, pw, by=me["username"], must_change=True)
                    st.session_state.issued_pw = (un.strip(), pw)
                    st.rerun()
                except auth.AuthError as e:
                    st.error(str(e))

        with c2:
            st.markdown("**ユーザーを変更**")
            others = [u for u in users if u["username"] != me["username"]]
            if not others:
                st.caption("自分以外のユーザーがいません")
            else:
                target = st.selectbox("対象", [u["username"] for u in others],
                                      format_func=lambda n: f"{n}（{next(u['display_name'] for u in others if u['username'] == n)}）")
                tu = next(u for u in others if u["username"] == target)
                new_role = st.selectbox("役割", list(auth.ROLES), index=list(auth.ROLES).index(tu["role"]),
                                        format_func=lambda r: auth.ROLES[r], key="edit_role")
                b1, b2, b3 = st.columns(3)
                try:
                    if b1.button("役割を保存", disabled=new_role == tu["role"]):
                        auth.set_role(target, new_role, me["username"]); st.rerun()
                    if b2.button("無効にする" if tu["active"] else "有効にする"):
                        auth.set_active(target, not tu["active"], me["username"]); st.rerun()
                    if b3.button("パスワード再発行"):
                        st.session_state.issued_pw = (target, auth.reset_password(target, me["username"]))
                        st.rerun()
                except auth.AuthError as e:
                    st.error(str(e))
                st.caption("退職などで使わなくなった人は「無効にする」。記録を残すため削除はしません。"
                           "再発行するとロックも解除されます。")

    with t_settings:
        st.caption("会計CSVの科目コードなどを変更できます。変更は次回の集計から反映されます。")
        stored = db.get_settings()
        base = load_config(with_db=False)
        with st.form("settings"):
            vals = {}
            for d in settings.DEFS:
                cur = settings.current(cfg, d["key"])
                label = d["label"] + ("　✎変更済み" if d["key"] in stored else "")
                if d["kind"] == "bool":
                    vals[d["key"]] = st.toggle(label, value=bool(cur), help=d.get("help"))
                elif d["kind"] == "select":
                    opts = list(d["options"])
                    vals[d["key"]] = st.selectbox(label, opts, index=opts.index(cur) if cur in opts else 0,
                                                  format_func=lambda o, d=d: d["options"][o], help=d.get("help"))
                else:
                    vals[d["key"]] = st.text_input(label, value=str(cur), help=d.get("help"))
            if st.form_submit_button("保存する", type="primary"):
                try:
                    changed = []
                    for k, v in vals.items():
                        s = settings.validate(k, v)
                        old = settings.current(cfg, k)
                        old_s = settings.validate(k, old)
                        if s != old_s:
                            db.set_setting(k, s, me["username"])
                            changed.append(f"{settings.BY_KEY[k]['label']}: {old_s} → {s}")
                    if changed:
                        db.log(me["username"], "設定変更", " / ".join(changed))
                        st.success("保存しました：" + " / ".join(changed))
                        st.rerun()
                    else:
                        st.info("変更はありません")
                except ValueError as e:
                    st.error(str(e))
        if stored and st.button("すべて初期値に戻す"):
            for k in stored:
                db.delete_setting(k)
            db.log(me["username"], "設定変更", "すべて初期値に戻す")
            st.rerun()
        with st.expander("初期値（config.yaml）"):
            st.table(pd.DataFrame([{"項目": d["label"], "初期値": str(settings.current(base, d["key"]))}
                                   for d in settings.DEFS]))

    with t_logs:
        c1, c2 = st.columns(2)
        fu = c1.selectbox("ユーザー", ["（すべて）"] + [u["username"] for u in auth.list_users()])
        acts = ["（すべて）", "ログイン", "ログイン失敗", "ログアウト", "集計実行", "集計失敗", "修正", "修正取消",
                "ダウンロード", "ファイル保存", "ユーザー追加", "役割変更", "有効化", "無効化",
                "パスワード再発行", "パスワード変更", "設定変更"]
        fa = c2.selectbox("操作", acts)
        logs = pd.DataFrame(db.recent_logs(1000, None if fu.startswith("（") else fu,
                                           None if fa.startswith("（") else fa))
        if logs.empty:
            st.caption("該当するログはありません")
        else:
            logs.columns = ["日時", "ユーザー", "操作", "内容"]
            st.dataframe(logs, width="stretch", hide_index=True)
            st.download_button("⬇ 表示中のログをCSVで保存", logs.to_csv(index=False).encode("utf-8-sig"),
                               file_name=f"操作ログ_{datetime.now():%Y%m%d}.csv",
                               on_click=db.log, args=(me["username"], "ダウンロード", "操作ログ"))


# =============================================================================
# メイン
# =============================================================================
user = require_login()
with st.sidebar:
    st.subheader("⛽ ガソリンカード費用振替")
    st.text(f"{user['display_name']}（{user['role_label']}）")  # 表示名はHTMLとして解釈させない
    allowed = [p for p, roles in PAGES.items() if user["role"] in roles]
    page = st.radio("メニュー", allowed, horizontal=True, label_visibility="collapsed")
    cols = st.columns(2)
    if cols[0].button("ログアウト", width="stretch"):
        logout()
        st.rerun()
    with cols[1].popover("PW変更", width="stretch"):
        form_change_password(user, forced=False)

if page == "集計":
    page_aggregate(user)
elif page == "月次推移":
    page_trend()
elif page == "管理":
    page_admin(user)
