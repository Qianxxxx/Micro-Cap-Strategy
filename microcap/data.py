import json
from pathlib import Path

import polars as pl


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
KEY = ["date", "code"]
TABLES = {
    "price": "price/*.parquet",
    "fundamentals": "fundamentals/*.parquet",
    "active_flow": "active_flow/*.parquet",
    "index_daily": "index/8841431.WI.parquet",
    "csi300_total_return": "index/H00300.CSI.parquet",
    "csi300_members": "index/000300.SH_members.parquet",
    "csi300_changes": "index/000300.SH_changes.parquet",
    "adj_factor": "adj_factor/adj_factor.parquet",
}


def scan(table):
    return pl.scan_parquet(DATA / TABLES[table]).with_columns(pl.col("code").cast(pl.String))


def indices():
    micro = scan("index_daily").select("date", pl.col("close").alias("micro"))
    csi = scan("csi300_total_return").select("date", pl.col("close").alias("csi300"))
    result = micro.join(csi, on="date", how="full", coalesce=True, validate="1:1").sort("date").collect()
    if result.select(pl.any_horizontal(pl.all().is_null()).any()).item():
        raise ValueError("Index trading calendars do not match")
    return result


def audit():
    import pymysql

    config = json.loads((ROOT / "config/mysql.local.json").read_text(encoding="utf-8"))
    result = []
    with pymysql.connect(**config, connect_timeout=5, read_timeout=120) as connection:
        with connection.cursor() as cursor:
            cursor.execute("START TRANSACTION READ ONLY")
            for table in TABLES:
                local = scan(table).select(
                    pl.len().alias("rows"), pl.col("date").min().alias("first"),
                    pl.col("date").max().alias("last"),
                ).collect().row(0)
                cursor.execute(f"SELECT COUNT(*), MIN(date), MAX(date) FROM `{table}`")
                remote = cursor.fetchone()
                if local != remote:
                    raise ValueError(f"{table}: Parquet {local} differs from MySQL {remote}")
                result.append((table, *local))
            for view in ("backtest_price", "backtest_fundamentals"):
                cursor.execute(f"SELECT * FROM `{view}` LIMIT 1")
                if cursor.fetchone() is None:
                    raise ValueError(f"{view} is empty")
    return pl.DataFrame(result, schema=["table", "rows", "first", "last"], orient="row")
