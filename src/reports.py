"""月次推移レポート（SQLで集計）

facility_monthly テーブル（CSVを出力できた月の施設別確定金額）を SQL で集計する。
"""
from __future__ import annotations

import pandas as pd

from . import db

# 月ごとの合計（直接利用分・按分受入分の内訳つき）
SQL_MONTHLY_TOTAL = """
SELECT month                AS 対象月,
       COUNT(*)             AS 施設数,
       SUM(direct_amount)   AS 直接利用,
       SUM(shared_amount)   AS 按分受入,
       SUM(total_amount)    AS 合計
FROM facility_monthly
GROUP BY month
ORDER BY month
"""

# 施設別の月次推移と前月差（ウィンドウ関数 LAG）
SQL_FACILITY_TREND = """
SELECT month                                   AS 対象月,
       facility_code                           AS 施設コード,
       facility_name                           AS 施設名,
       total_amount                            AS 金額,
       total_amount - LAG(total_amount) OVER (
           PARTITION BY facility_code ORDER BY month
       )                                       AS 前月差
FROM facility_monthly
ORDER BY facility_code, month
"""

# 指定月の施設ランキング（金額の大きい順）と構成比
SQL_RANKING = """
SELECT RANK() OVER (ORDER BY total_amount DESC)                      AS 順位,
       facility_code                                                 AS 施設コード,
       facility_name                                                 AS 施設名,
       direct_amount                                                 AS 直接利用,
       shared_amount                                                 AS 按分受入,
       total_amount                                                  AS 合計,
       ROUND(100.0 * total_amount / SUM(total_amount) OVER (), 1)    AS 構成比
FROM facility_monthly
WHERE month = ?
ORDER BY total_amount DESC
"""

# 実行の記録（直近）
SQL_RUNS = """
SELECT run_at AS 実行日時, month AS 対象月, username AS 実行者, card_count AS カード数,
       pdf_total AS 請求合計, csv_total AS CSV合計, error_count AS エラー件数,
       CASE exported WHEN 1 THEN '出力済' ELSE '未出力' END AS CSV
FROM runs
ORDER BY id DESC
LIMIT ?
"""


def _query(sql: str, params: tuple = ()) -> pd.DataFrame:
    with db.connect() as con:
        return pd.read_sql_query(sql, con, params=params)


def monthly_total() -> pd.DataFrame:
    return _query(SQL_MONTHLY_TOTAL)


def facility_trend() -> pd.DataFrame:
    return _query(SQL_FACILITY_TREND)


def ranking(month: str) -> pd.DataFrame:
    return _query(SQL_RANKING, (month,))


def runs(limit: int = 50) -> pd.DataFrame:
    return _query(SQL_RUNS, (limit,))


def months() -> list[str]:
    return list(_query("SELECT DISTINCT month FROM facility_monthly ORDER BY month DESC")["month"])
