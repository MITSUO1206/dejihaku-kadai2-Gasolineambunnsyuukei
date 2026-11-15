"""公開デモ用モード

安全側に倒すため「既定でデモモード」。社内で実データを扱うときだけ GASCARD_DEMO=0 にする（start.bat が自動で設定）。
クラウドに置いたときに設定を忘れても、実データのアップロードや管理者の乗っ取りが起きないようにするため。

- 架空データ（入力ファイル見本）だけで動かす。ファイルのアップロードは受け付けない
- デモ用アカウント3つを自動で作り、ログイン画面に表示する
- デモ用アカウントはパスワード変更・無効化・役割変更・ロックの対象外（いたずらで他の人が入れなくなるのを防ぐ）
"""
from __future__ import annotations

import os
import shutil

from .common import ROOT

PASSWORD = "demo1234"
USERS = [("demo_admin", "デモ 管理者", "admin"), ("demo_staff", "デモ 担当者", "staff"),
         ("demo_viewer", "デモ 閲覧者", "viewer")]
SAMPLES = {"2026-10": "ダミーデータ_2026-10", "2026-11": "ダミーデータ_2026-11"}


def enabled() -> bool:
    return os.environ.get("GASCARD_DEMO", "1") != "0"


def is_protected(username: str) -> bool:
    return enabled() and username in {u for u, _, _ in USERS}


def seed(cfg: dict) -> None:
    """デモ用アカウントと架空の入力ファイルを用意する（何度呼んでもよい）"""
    from . import auth
    for username, name, role in USERS:
        if auth.get_user(username) is None:
            auth.create_user(username, name, role, PASSWORD, by="(デモ初期化)", must_change=False)
    for month, folder in SAMPLES.items():
        dst = ROOT / cfg["paths"]["input_dir"] / month
        src = ROOT / "入力ファイル見本" / folder
        if src.exists() and not dst.exists():
            shutil.copytree(src, dst)
