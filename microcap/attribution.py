from math import sqrt

import polars as pl


def simulated_returns(panel, ranked, calendar, index):
    # Apply the close membership to the following trading session.
    holdings = ranked.join(calendar, on="date").with_columns(session=pl.col("session") + 1)
    returns = panel.select("session", "code", "ret")
    buckets = (
        holdings.join(returns, on=["session", "code"], how="left", validate="m:1")
        .with_columns(bucket=pl.when(pl.col("rank") <= 400).then(pl.lit("micro")).otherwise(pl.lit("adjacent")))
        .group_by("session", "bucket").agg(
            pl.col("ret").fill_null(0).mean().alias("return"),
            pl.col("ret").is_not_null().mean().alias("coverage"),
            pl.len().alias("members"),
        )
        .pivot(on="bucket", index="session", values=["return", "coverage", "members"])
    )
    return (
        calendar.join(buckets, on="session").join(index, on="date").sort("date")
        .with_columns(wind_return=pl.col("micro").pct_change())
    )


def tracking(simulated):
    return simulated.select(
        pl.corr("return_micro", "wind_return").alias("daily_correlation"),
        ((pl.col("return_micro") - pl.col("wind_return")).std() * sqrt(252)).alias("annual_tracking_error"),
        pl.col("coverage_micro").mean().alias("mean_micro_coverage"),
        ((1 + pl.col("return_micro")).product() ** (252 / pl.len()) - 1).alias("micro_annual_return"),
        ((1 + pl.col("return_adjacent")).product() ** (252 / pl.len()) - 1).alias("adjacent_annual_return"),
    )


def phase_risk(width):
    daily = width.sort("date").with_columns(
        relative=(pl.col("micro").pct_change() - pl.col("all_a_return")),
        ratio=(1 + pl.col("micro").pct_change()) / (1 + pl.col("all_a_return")),
        year=pl.col("date").dt.year(),
    ).filter(pl.col("year") >= 2013)
    return (
        daily.with_columns(
            phase=pl.when(pl.col("year") <= 2016).then(pl.lit("2013-2016"))
            .when(pl.col("year") <= 2023).then(pl.lit("2017-2023")).otherwise(pl.lit("2024-2026"))
        )
        .with_columns(nav=pl.col("ratio").cum_prod().over("phase"))
        .group_by("phase").agg(
            (pl.col("nav") / pl.col("nav").cum_max().clip(lower_bound=1) - 1).min().alias("relative_drawdown"),
            (pl.col("relative").filter(pl.col("relative") < 0).std() * sqrt(252)).alias("down_day_volatility"),
            pl.col("relative").sort().head((pl.len() * 0.01).ceil().cast(pl.UInt32)).mean().alias("worst_one_percent"),
        ).sort("phase")
    )


def rebalance_attribution(panel, ranked, calendar, start):
    from collections import defaultdict
    from math import log1p

    members = {
        day: codes
        for day, codes in ranked.filter(pl.col("rank") <= 400)
        .group_by("date").agg("code").iter_rows()
    }
    dates = calendar.filter(pl.col("date") >= start)["date"].to_list()
    stock_returns = panel.filter(pl.col("date") >= start).select("date", "code", "ret")
    stock_returns = {
        day: dict(zip(codes, returns))
        for day, codes, returns in stock_returns.group_by("date").agg("code", "ret").iter_rows()
    }
    pairs = [("quarter", "month"), ("month", "week"), ("week", "day")]

    def period(day, frequency):
        if frequency == "quarter":
            return day.year, (day.month - 1) // 3
        if frequency == "month":
            return day.year, day.month
        if frequency == "week":
            return day.isocalendar()[:2]
        return day

    contributions = defaultdict(float)
    for slow, fast in pairs:
        books = [{}, {}, {}]
        for i, day in enumerate(dates):
            if i:
                returns = stock_returns.get(day, {})
                gross = []
                for book in books:
                    for code in book:
                        book[code] *= 1 + (returns.get(code) or 0)
                    growth = sum(book.values())
                    gross.append(growth)
                    for code in book:
                        book[code] /= growth
                selection = log1p(gross[1] - 1) - log1p(gross[0] - 1)
                reweight = log1p(gross[2] - 1) - log1p(gross[1] - 1)
                contributions[slow, fast, "selection"] += selection
                contributions[slow, fast, "equal_weight"] += reweight
            if i == len(dates) - 1:
                break
            next_day = dates[i + 1]
            basket = members[day]
            equal = dict.fromkeys(basket, 1 / len(basket))
            slow_reset = i == 0 or period(day, slow) != period(next_day, slow)
            fast_reset = i == 0 or period(day, fast) != period(next_day, fast)
            if slow_reset:
                books[0] = equal.copy()
                books[1] = equal.copy()
            elif fast_reset:
                # Keep the weights of retained names and split sale proceeds across additions.
                retained = {code: books[1][code] for code in basket if code in books[1]}
                added = [code for code in basket if code not in retained]
                if added:
                    proceeds = 1 - sum(retained.values())
                    retained.update(dict.fromkeys(added, proceeds / len(added)))
                elif retained:
                    total = sum(retained.values())
                    retained = {code: weight / total for code, weight in retained.items()}
                books[1] = retained
            if fast_reset:
                books[2] = equal.copy()
    annualizer = 252 / (len(dates) - 1)
    rows = []
    for slow, fast in pairs:
        selection = contributions[slow, fast, "selection"] * annualizer
        reweight = contributions[slow, fast, "equal_weight"] * annualizer
        rows.append((slow, fast, selection, reweight, selection + reweight))
    return pl.DataFrame(
        rows, schema=["slow", "fast", "selection", "equal_weight", "total"], orient="row"
    )


def monthly_stock_distribution(panel, ranked, calendar, start):
    month_ends = (
        calendar.filter(pl.col("date") >= start)
        .with_columns(month=pl.col("date").dt.truncate("1mo"))
        .group_by("month").agg(pl.col("date").max()).sort("month")
        .with_columns(end=pl.col("date").shift(-1)).drop_nulls("end")
    )
    closes = panel.select("date", "code", "adjusted")
    observations = (
        ranked.join(month_ends, on="date").join(closes, on=["date", "code"])
        .join(closes.rename({"date": "end", "adjusted": "ending_close"}), on=["end", "code"])
        .with_columns(
            bucket=pl.when(pl.col("rank") <= 400).then(pl.lit("micro")).otherwise(pl.lit("adjacent")),
            monthly_return=pl.col("ending_close") / pl.col("adjusted") - 1,
        )
    )
    ret = pl.col("monthly_return")
    return observations.group_by("bucket").agg(
        pl.len().alias("observations"),
        ret.skew().alias("skew"),
        ret.quantile(0.99, interpolation="linear").alias("p99"),
        (ret > 0.5).mean().alias("share_above_50pct"),
        (ret < -0.1).mean().alias("share_below_minus_10pct"),
        (ret < 0).mean().alias("share_negative"),
    ).sort("bucket")
