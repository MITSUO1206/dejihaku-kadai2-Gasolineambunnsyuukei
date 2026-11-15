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
# 集計ページ（管理者・担当者）
# =============================================================================
UPLOAD_KINDS = {  # 種類: (ラベル, 拡張子, ファイル名の頭, 中身の先頭バイト)
    "pdf": ("利用明細（PDF）", "pdf", "利用明細_", b"%PDF-"),
    "ledger": ("管理台帳（Excel）", "xlsx", "管理台帳_", b"PK"),
    "alloc": ("給与按分資料（Excel）", "xlsx", "給与按分_", b"PK"),
}
DEMO_MAX_BYTES = 10 * 1024 * 1024


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


def page_aggregate(user: dict) -> None:
    who = f"{user['display_name']}（{user['username']}）"
    cfg = workspace_cfg()

    def months_available() -> list[str]:
        d = ROOT / cfg["paths"]["input_dir"]
        return sorted([p.name for p in d.iterdir() if p.is_dir()], reverse=True) if d.exists() else []

    def execute(month: str, use_ai: bool) -> None:
        with st.spinner("照合・集計・按分を実行中…"):
            try:
                st.session_state.res = run(month, cfg, use_ai=use_ai, user=user["username"])
                st.session_state.run_error = None
            except Exception as e:  # 入力ファイル不足・列名違いなど
                st.session_state.res = None
                st.session_state.run_error = str(e)
                db.log(user["username"], "集計失敗", f"{month} {e}")

    with st.sidebar:
        st.divider()
        existing = months_available()
        month = st.text_input("対象月（利用月）", value=existing[0] if existing else "2026-10",
                              help="YYYY-MM 形式。例: 2026-10").strip()
        # フォルダ名に使うので、形式を厳密に確認する（../ などでフォルダ外に書き込ませない）
        if not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", month):
            st.error("対象月は 2026-10 のように YYYY-MM 形式で入力してください")
            st.stop()
        has_key = ai_key_configured()
        use_ai = st.toggle("AI読取補助を使う", value=cfg["ai"]["enabled"] and has_key, disabled=not has_key,
                           help="ルールで読めなかった明細行だけをClaude APIに送ります。カード番号・名義は送りません。")
        if not has_key:
            st.caption("🔑 APIキー未設定（AI読取補助は使えません）")
        elif demo.enabled():
            from src.ai_assist import daily_limit
            st.caption(f"🔑 AI読取補助：同じ行は前回の結果を再利用し、問い合わせは1日{daily_limit()}回までに制限しています")
        else:
            st.caption("🔑 APIキー: 設定済み")

        with st.expander("入力ファイルを置く", expanded=demo.enabled()):
            if demo.enabled():
                st.warning("公開デモです。実際の明細や個人情報を含むファイルは上げないでください。"
                           "上げたファイルはあなた専用の場所に置かれ、1日で自動的に消えます。（1ファイル10MBまで）")
                with st.popover("試せるサンプルファイル"):
                    for sm, folder in demo.SAMPLES.items():
                        st.caption(f"{sm}（{'エラー入り' if sm == '2026-10' else 'エラーなし'}）")
                        for f in sorted((ROOT / "入力ファイル見本" / folder).iterdir()):
                            st.download_button(f"⬇ {f.name}", f.read_bytes(), file_name=f.name, key=f"smp{f.name}")
            in_dir = ROOT / cfg["paths"]["input_dir"] / month
            ups = {k: st.file_uploader(v[0], type=v[1], key=f"up_{k}") for k, v in UPLOAD_KINDS.items()}
            if st.button("保存する", disabled=not any(ups.values())):
                problems = []
                for k, f in ups.items():
                    data = f.getvalue() if f else b""
                    if f and not data.startswith(UPLOAD_KINDS[k][3]):
                        problems.append(f"{f.name} は{UPLOAD_KINDS[k][0]}のファイルではありません")
                    elif f and demo.enabled() and len(data) > DEMO_MAX_BYTES:
                        problems.append(f"{f.name} が大きすぎます（デモは10MBまで）")
                if problems:
                    st.error("保存しませんでした：" + " ／ ".join(problems))
                else:
                    in_dir.mkdir(parents=True, exist_ok=True)
                    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    saved = []
                    for k, f in ups.items():
                        if not f:
                            continue
                        _, ext, prefix, _ = UPLOAD_KINDS[k]
                        # 同じ種類の古いファイルは「置き換え前」へ移す（古い方を読んでしまわないように）
                        for old in in_dir.glob(f"{prefix}*.{ext}"):
                            bk = in_dir / "置き換え前" / stamp
                            bk.mkdir(parents=True, exist_ok=True)
                            old.rename(bk / old.name)
                        name = Path(f.name).name
                        name = name if name.startswith(prefix) else prefix + name
                        (in_dir / name).write_bytes(f.getvalue())
                        saved.append(name)
                    db.log(user["username"], "ファイル保存", f"{month} {', '.join(saved)}")
                    st.success("保存しました。「実行」を押すと、このファイルで集計します。")
            if in_dir.exists():
                st.caption("現在のファイル: " + ", ".join(p.name for p in sorted(in_dir.iterdir()) if p.is_file()))

        if st.button("▶ 実行（再集計）", type="primary", width="stretch"):
            execute(month, use_ai)

    if st.session_state.get("run_error"):
        st.error(f"実行できませんでした: {st.session_state.run_error}")
    res = st.session_state.get("res")
    if res is None or res.month != month:
        st.title("集計")
        st.info("左のメニューで対象月を確認し、「実行」を押してください。")
        return

    issues = res.issues
    n_err = sum(1 for i in issues if i.severity == ERROR)
    n_warn = sum(1 for i in issues if i.severity == WARNING)
    st.title(f"{month_label(month)}分 ガソリンカード集計")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("カード数（明細）", f"{len(res.cards)} 枚")
    c2.metric("PDF請求合計", f"¥{res.totals['PDF請求合計']:,}")
    c3.metric("CSV合計", f"¥{res.totals['CSV借方合計']:,}")
    c4.metric("エラー", f"{n_err} 件")
    c5.metric("要確認", f"{n_warn} 件")
    if res.can_export:
        st.success("エラーはありません。明細合計・按分後合計・CSV合計が一致しています。費用振替CSVを出力できます。")
    else:
        st.warning(f"エラーが {n_err} 件あります。「エラー確認・修正」タブで修正し、再集計してください。"
                   f"（保留中の金額 ¥{res.totals['エラーで保留中の金額']:,}）")

    tabs = st.tabs(["🛠 エラー確認・修正", "✅ 整合性チェック", "💳 カード別一覧", "🏢 施設別集計", "📄 利用明細",
                    "📥 出力", "📝 修正履歴"])
    fac = res.ledger.facilities
    fac_options = [f"{r.facility_code} {r.facility_name}" for r in fac.itertuples()]

    def rerun_after_save(msg: str) -> None:
        execute(month, use_ai)
        st.toast(msg + " → 再集計しました")
        st.rerun()

    with tabs[0]:
        df = pd.DataFrame([i.to_row() for i in issues])
        if df.empty:
            st.success("指摘はありません")
        else:
            df["カード番号"] = df["カード番号"].map(lambda x: format_card_no(x) if x else "")
            sev = st.multiselect("重要度", [ERROR, WARNING, INFO], default=[ERROR, WARNING])
            st.dataframe(df[df["重要度"].isin(sev)], width="stretch", hide_index=True)

        st.subheader("エラーの修正")
        st.caption("修正内容は元のExcel・PDFを書き換えずに修正ファイルへ保存され、再集計のたびに反映されます。"
                   "恒久的な変更（カードの新規登録など）は管理台帳・按分資料も更新してください。")
        fixable = [i for i in issues if i.severity == ERROR]
        if not fixable:
            st.info("修正が必要なエラーはありません")
        seen_emp: set[str] = set()
        for n, iss in enumerate(fixable):
            if iss.code.startswith("A"):
                emp = iss.ref.get("employee_no", "")
                if emp in seen_emp:
                    continue
                seen_emp.add(emp)
            label = f"[{iss.code}] {iss.title} — {format_card_no(iss.card_no) if iss.card_no else ''} {iss.user}"
            with st.expander(label):
                st.write(f"**詳細**: {iss.detail}")
                st.write(f"**対処**: {iss.action}")

                if iss.code in ("P001", "P002"):
                    default = iss.ref.get("subtotal") or iss.ref.get("read_sum") or 0
                    amt = st.number_input("このカードの当月利用金額（PDF原本で確認した金額）", value=int(default),
                                          step=1, key=f"amt{n}")
                    if st.button("金額を確定", key=f"b{n}"):
                        save_correction(cfg, month, "pdf_overrides", iss.card_no, {"amount": int(amt)}, who,
                                        f"{iss.code}: {iss.detail}")
                        rerun_after_save("金額を保存しました")

                elif iss.code in ("M001", "M002", "L001", "L003", "L004", "M004"):
                    cats = ["施設", "AM", "アドバイザー"]
                    f_sel = st.selectbox("利用施設", fac_options, key=f"f{n}")
                    cat = st.selectbox("区分", cats, key=f"c{n}")
                    emp = st.text_input("社員番号（AM・アドバイザーの場合）", key=f"e{n}")
                    uname = st.text_input("利用者", value=iss.user, key=f"u{n}")
                    if st.button("このカードの情報を確定", key=f"b{n}"):
                        save_correction(cfg, month, "card_overrides", iss.card_no, {
                            "facility_code": f_sel.split(" ")[0], "category": cat, "employee_no": emp,
                            "user_name": uname, "card_holder": iss.ref.get("holder", "")}, who,
                            f"{iss.code}: {iss.detail}")
                        rerun_after_save("カード情報を保存しました")

                elif iss.code in ("A001", "A002", "A003"):
                    emp = iss.ref.get("employee_no", "")
                    plan = pd.DataFrame(iss.ref.get("plan") or [{"facility_code": fac_options[0].split(" ")[0],
                                                                 "ratio": 100.0}])
                    st.write(f"社員番号 **{emp}** の按分先と割合（合計100%）")
                    edited = st.data_editor(
                        plan, num_rows="dynamic", key=f"pl{n}", width="stretch",
                        column_config={
                            "facility_code": st.column_config.SelectboxColumn(
                                "按分先施設コード", options=list(fac["facility_code"]), required=True),
                            "ratio": st.column_config.NumberColumn("按分割合(%)", min_value=0, max_value=100,
                                                                   required=True)})
                    total = float(edited["ratio"].fillna(0).sum())
                    st.write(f"合計: **{total:g}%**")
                    if st.button("按分を確定", key=f"b{n}", disabled=abs(total - 100) > 1e-6):
                        save_correction(cfg, month, "alloc_overrides", emp,
                                        edited.dropna().to_dict("records"), who, f"{iss.code}: {iss.detail}")
                        rerun_after_save("按分を保存しました")
                else:
                    st.info("この種類のエラーは画面からは修正できません。元データを確認してください。")

    with tabs[1]:
        t = pd.DataFrame([{"項目": k, "金額": f"¥{v:,}"} for k, v in res.totals.items()])
        st.table(t)
        st.write("判定: " + ("✅ 一致" if res.totals_ok else "❌ 不一致（エラー保留分を含め確認してください）"))
        st.caption("確認内容：PDF請求合計 ＝ 明細読取合計 ＝ 按分前合計＋保留金額、按分前合計 ＝ 按分後合計 ＝ CSV合計")

    sheets = summary_sheets(res)
    with tabs[2]:
        st.dataframe(sheets["カード別一覧"], width="stretch", hide_index=True)
    with tabs[3]:
        st.dataframe(sheets["施設別集計"], width="stretch", hide_index=True)
    with tabs[4]:
        st.dataframe(sheets["利用明細"], width="stretch", hide_index=True)

    with tabs[5]:
        for key, label in [("csv", "費用振替CSV"), ("summary", "カード別・施設別集計一覧（Excel）"),
                           ("errors", "エラー一覧（Excel）")]:
            p: Path | None = res.outputs.get(key)
            if p and p.exists():
                st.download_button(f"⬇ {label}", p.read_bytes(), file_name=p.name, key=f"dl{key}",
                                   on_click=db.log, args=(user["username"], "ダウンロード", f"{month} {p.name}"))
            elif key == "csv":
                st.button(f"⬇ {label}（エラー解消後に出力されます）", disabled=True)
        if res.can_export:
            st.subheader("CSVプレビュー")
            st.dataframe(res.journal, width="stretch", hide_index=True)
        if not demo.enabled():
            st.caption(f"出力先フォルダ: {ROOT / cfg['paths']['output_dir'] / month}")

    with tabs[6]:
        corr = load_corrections(cfg, month)
        if not demo.enabled():
            st.caption(f"修正ファイル: {corrections_path(cfg, month)}")
        for kind, label in [("pdf_overrides", "金額の補正"), ("card_overrides", "カード情報の補正"),
                            ("alloc_overrides", "按分の補正")]:
            st.markdown(f"**{label}**")
            if not corr[kind]:
                st.caption("なし")
            for key, val in corr[kind].items():
                cols = st.columns([5, 1])
                cols[0].code(f"{key}: {val}")
                if cols[1].button("取消", key=f"del{kind}{key}"):
                    save_correction(cfg, month, kind, key, None, who, "取消")
                    rerun_after_save("補正を取り消しました")
        if corr["history"]:
            st.markdown("**操作履歴**")
            st.dataframe(pd.DataFrame(corr["history"]).iloc[::-1], width="stretch", hide_index=True)


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
