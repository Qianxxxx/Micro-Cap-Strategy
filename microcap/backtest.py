from math import sqrt

import polars as pl


def backtest(frame, start):
    names = ["width", "flow", "combined"]
    frame = frame.with_columns(
        micro_return=pl.col("micro").pct_change(),
        csi_return=pl.col("csi300").pct_change(),
    )
    for name in names:
        signal = pl.col(f"{name}_score")
        # Equal or temporarily missing scores carry the previous signal.
        target = pl.when(signal > 0.5).then(1).when(signal < 0.5).then(0)
        frame = frame.with_columns(target.forward_fill().alias(f"{name}_target"))
    frame = frame.filter(pl.col("date") >= start)
    for name in names:
        target = pl.col(f"{name}_target")
        frame = frame.with_columns(
            target.shift(1).forward_fill().fill_null(0).alias(f"{name}_close_weight"),
            target.shift(2).forward_fill().fill_null(0).alias(f"{name}_return_weight"),
        )
    return frame


def performance(daily, fee):
    daily = daily.with_columns(
        (pl.col("micro_return") + pl.col("csi_return")).truediv(2).alias("benchmark_return"),
        pl.int_range(pl.len()).alias("row"),
    )
    names = ["width", "flow", "combined"]
    for name in names:
        weight = pl.col(f"{name}_return_weight")
        turnover = 2 * (pl.col(f"{name}_close_weight") - weight).abs()
        gross = 1 + weight * pl.col("micro_return") + (1 - weight) * pl.col("csi_return")
        daily = daily.with_columns(turnover.alias(f"{name}_turnover")).with_columns(
            ((gross * (1 - fee * turnover)) - 1).alias(f"{name}_return"),
            (fee * turnover).alias(f"{name}_fee"),
        )
    assets = ["micro", "csi", "benchmark"] + names
    daily = daily.with_columns(
        pl.when(pl.col("row") == 0).then(0.0).otherwise(pl.col(f"{name}_return")).alias(f"{name}_return")
        for name in assets
    ).with_columns(
        (1 + pl.col(f"{name}_return")).cum_prod().alias(f"{name}_nav") for name in assets
    )
    for name in names:
        daily = daily.with_columns(
            (pl.col(f"{name}_nav") / pl.col("benchmark_nav")).alias(f"{name}_relative_nav"),
            ((1 + pl.col(f"{name}_return")) / (1 + pl.col("benchmark_return")) - 1)
            .alias(f"{name}_relative_return"),
        )
    summaries = [
        portfolio_metrics(name, daily[f"{name}_nav"], daily[f"{name}_return"], daily["date"])
        for name in assets + [f"{name}_relative" for name in names]
    ]
    annual = daily.with_columns(pl.col("date").dt.year().alias("year")).group_by("year").agg(
        ((1 + pl.col(f"{name}_return")).product() - 1).alias(name)
        for name in assets + [f"{name}_relative" for name in names]
    ).sort("year")
    return daily.drop("row"), pl.DataFrame(summaries), annual



def portfolio_metrics(name, nav, returns, dates):
    years = (dates[-1] - dates[0]).days / 365.25
    cagr = nav[-1] ** (1 / years) - 1
    drawdown = (nav / nav.cum_max() - 1).min()
    return {
        "portfolio": name, "annual_return": cagr,
        "annual_volatility": returns[1:].std() * sqrt(252), "max_drawdown": drawdown,
        "return_drawdown": cagr / abs(drawdown) if drawdown else None,
    }
