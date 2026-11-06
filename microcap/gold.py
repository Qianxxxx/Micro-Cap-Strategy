import polars as pl

from .backtest import portfolio_metrics
from .data import DATA


def gold_returns(calendar):
    prices = (
        pl.read_parquet(DATA / "index/AU9999.SGE.parquet", columns=["date", "close"])
        .rename({"close": "gold_close"})
    )
    aligned = calendar.join(prices, on="date", how="left", validate="1:1").sort("date")
    close = pl.col("gold_close")
    if aligned.filter(close.is_null() | ~close.is_finite() | (close <= 0)).height:
        raise ValueError("AU9999.SGE needs one valid closing price per backtest date")
    return aligned.with_columns(gold_return=close.pct_change().fill_null(0))


def gold_allocation(core, gold, weights):
    if not weights or len(set(weights)) != len(weights) or any(not 0 < w < 1 for w in weights):
        raise ValueError("Gold weights must be distinct fractions between zero and one")
    inputs = core.select("date", "combined_return", "benchmark_return").join(
        gold, on="date", how="left", validate="1:1"
    )
    if inputs["gold_return"].null_count():
        raise ValueError("Gold return series does not cover the core portfolio")
    names = ["strategy", "benchmark", "relative"]
    daily = []
    metrics = []
    for weight in weights:
        gold_leg = weight * pl.col("gold_return")
        frame = inputs.with_columns(
            gold_weight=pl.lit(weight),
            strategy_return=(1 - weight) * pl.col("combined_return") + gold_leg,
            benchmark_return=(1 - weight) * pl.col("benchmark_return") + gold_leg,
        ).with_columns(
            relative_return=(1 + pl.col("strategy_return")) / (1 + pl.col("benchmark_return")) - 1,
        ).with_columns(
            (1 + pl.col(f"{name}_return")).cum_prod().alias(f"{name}_nav") for name in names
        )
        for name in names:
            stats = portfolio_metrics(name, frame[f"{name}_nav"], frame[f"{name}_return"], frame["date"])
            metrics.append({"gold_weight": weight, **stats})
        daily.append(frame)
    daily = pl.concat(daily)
    annual = (
        daily.with_columns(year=pl.col("date").dt.year())
        .group_by("gold_weight", "year").agg(
            ((1 + pl.col(f"{name}_return")).product() - 1).alias(name) for name in names
        ).sort("gold_weight", "year")
    )
    return daily, pl.DataFrame(metrics), annual
