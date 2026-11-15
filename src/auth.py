"""ログイン認証とユーザー管理

- パスワードは bcrypt で暗号化して保存（元のパスワードは保存しない）
- 5回続けて間違えると15分ロック
- 管理者がパスワードを再発行すると、仮パスワードで入った後に必ず変更を求める
- 有効な管理者が0人になる操作（最後の管理者の無効化・役割変更）はできない
"""
from __future__ import annotations

import re
import secrets
import string
from datetime import datetime, timedelta

import bcrypt

from . import db, demo

ROLES = {"admin": "管理者", "staff": "担当者", "viewer": "閲覧者"}
MAX_FAILED = 5
LOCK_MINUTES = 15
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")


class AuthError(ValueError):
    """画面にそのまま表示できるメッセージを持つエラー"""


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def check_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode("utf-8"), hashed.encode("ascii"))
    except ValueError:
        return False


def validate_password(pw: str) -> None:
    if len(pw) < 8:
        raise AuthError("パスワードは8文字以上にしてください")
    if len(pw.encode("utf-8")) > 64:
        raise AuthError("パスワードが長すぎます（64バイトまで）")
    if not (re.search(r"[A-Za-z]", pw) and re.search(r"\d", pw)):
        raise AuthError("パスワードには英字と数字を両方含めてください")


def temp_password() -> str:
    alphabet = string.ascii_letters + string.digits
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(12))
        if re.search(r"[A-Za-z]", pw) and re.search(r"\d", pw):
            return pw


def _public(row) -> dict:
    d = dict(row)
    d.pop("password_hash", None)
    d["role_label"] = ROLES[d["role"]]
    return d


# ---- 参照 ----------------------------------------------------------------------
def user_count() -> int:
    with db.connect() as con:
        return con.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def list_users() -> list[dict]:
    with db.connect() as con:
        return [_public(r) for r in con.execute("SELECT * FROM users ORDER BY active DESC, role, username")]


def get_user(username: str) -> dict | None:
    with db.connect() as con:
        r = con.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    return _public(r) if r else None


def _guard_demo(username: str) -> None:
    if demo.is_protected(username):
        raise AuthError("デモ用アカウントは変更できません（デモでは、追加したユーザーで試してください）")


def _active_admins(con) -> int:
    return con.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND active = 1").fetchone()[0]


# ---- ログイン ------------------------------------------------------------------
def authenticate(username: str, password: str) -> dict:
    """成功したらユーザー情報を返す。失敗は AuthError（理由は利用者に必要以上に教えない）"""
    username = username.strip()
    with db.connect() as con:
        r = con.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if r is None:
            check_password(password, hash_password("dummy-password1"))  # 存在確認を時間差で悟らせない
            db.log(username or "-", "ログイン失敗", "存在しないユーザー")
            raise AuthError("ユーザー名またはパスワードが違います")
        if r["locked_until"] and datetime.fromisoformat(r["locked_until"]) > datetime.now():
            db.log(username, "ログイン失敗", "ロック中")
            raise AuthError(f"ログインに続けて失敗したため、{r['locked_until'][11:16]} までロックされています")
        if not check_password(password, r["password_hash"]):
            failed = r["failed_count"] + 1
            locked = (datetime.now() + timedelta(minutes=LOCK_MINUTES)).isoformat(timespec="seconds") \
                if failed >= MAX_FAILED and not demo.is_protected(username) else None
            con.execute("UPDATE users SET failed_count = ?, locked_until = ? WHERE id = ?",
                        (0 if locked else failed, locked, r["id"]))
            con.commit()
            db.log(username, "ログイン失敗", "パスワード違い" + ("（ロック）" if locked else ""))
            raise AuthError("ユーザー名またはパスワードが違います")
        if not r["active"]:
            db.log(username, "ログイン失敗", "無効化されたユーザー")
            raise AuthError("このユーザーは無効になっています。管理者に連絡してください")
        con.execute("UPDATE users SET failed_count = 0, locked_until = NULL, last_login_at = ? WHERE id = ?",
                    (db.now(), r["id"]))
        user = _public(r)
    db.log(username, "ログイン")
    return user


# ---- ユーザー管理（管理者用） ----------------------------------------------------
def create_user(username: str, display_name: str, role: str, password: str, by: str,
                must_change: bool = True) -> None:
    username, display_name = username.strip(), display_name.strip()
    if not USERNAME_RE.match(username):
        raise AuthError("ユーザー名は半角英数字と _ . - で3〜32文字にしてください")
    if not display_name:
        raise AuthError("表示名を入力してください")
    if role not in ROLES:
        raise AuthError("役割が正しくありません")
    validate_password(password)
    with db.connect() as con:
        if con.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            raise AuthError("そのユーザー名はすでに使われています")
        con.execute("""INSERT INTO users (username, display_name, role, password_hash, must_change,
                                          created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (username, display_name, role, hash_password(password), int(must_change), db.now(), db.now()))
    db.log(by, "ユーザー追加", f"{username}（{display_name}／{ROLES[role]}）")


def create_first_admin(username: str, display_name: str, password: str) -> None:
    """初回起動時だけ使える。ユーザーが1人でもいれば使えない"""
    if user_count() > 0:
        raise AuthError("すでにユーザーが登録されています")
    create_user(username, display_name, "admin", password, by=username, must_change=False)


def set_role(username: str, role: str, by: str) -> None:
    if role not in ROLES:
        raise AuthError("役割が正しくありません")
    _guard_demo(username)
    with db.connect() as con:
        r = con.execute("SELECT role, active FROM users WHERE username = ?", (username,)).fetchone()
        if r is None:
            raise AuthError("ユーザーが見つかりません")
        if r["role"] == "admin" and role != "admin" and r["active"] and _active_admins(con) <= 1:
            raise AuthError("有効な管理者が1人もいなくなるため変更できません")
        con.execute("UPDATE users SET role = ?, updated_at = ? WHERE username = ?", (role, db.now(), username))
    db.log(by, "役割変更", f"{username} → {ROLES[role]}")


def set_active(username: str, active: bool, by: str) -> None:
    _guard_demo(username)
    with db.connect() as con:
        r = con.execute("SELECT role, active FROM users WHERE username = ?", (username,)).fetchone()
        if r is None:
            raise AuthError("ユーザーが見つかりません")
        if not active and r["role"] == "admin" and r["active"] and _active_admins(con) <= 1:
            raise AuthError("有効な管理者が1人もいなくなるため無効にできません")
        con.execute("UPDATE users SET active = ?, failed_count = 0, locked_until = NULL, updated_at = ? "
                    "WHERE username = ?", (int(active), db.now(), username))
    db.log(by, "有効化" if active else "無効化", username)


def reset_password(username: str, by: str) -> str:
    """仮パスワードを発行して返す（画面に一度だけ表示する）。ロックも解除する"""
    _guard_demo(username)
    pw = temp_password()
    with db.connect() as con:
        cur = con.execute("UPDATE users SET password_hash = ?, must_change = 1, failed_count = 0, "
                          "locked_until = NULL, updated_at = ? WHERE username = ?",
                          (hash_password(pw), db.now(), username))
        if cur.rowcount == 0:
            raise AuthError("ユーザーが見つかりません")
    db.log(by, "パスワード再発行", username)
    return pw


def change_password(username: str, current: str, new: str) -> None:
    _guard_demo(username)
    with db.connect() as con:
        r = con.execute("SELECT password_hash FROM users WHERE username = ?", (username,)).fetchone()
        if r is None or not check_password(current, r["password_hash"]):
            raise AuthError("現在のパスワードが違います")
        if current == new:
            raise AuthError("今と違うパスワードにしてください")
        validate_password(new)
        con.execute("UPDATE users SET password_hash = ?, must_change = 0, updated_at = ? WHERE username = ?",
                    (hash_password(new), db.now(), username))
    db.log(username, "パスワード変更")
