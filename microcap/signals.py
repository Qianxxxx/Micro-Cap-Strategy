import polars as pl

from .data import KEY, scan


def stock_panel(end, calendar):
    price = scan("price").select(*KEY, "close", "amount")
    fields = ["industry", "total_market_cap", "is_st", "is_limit_up", "listing_date", "delisting_date"]
    stock = price.join(scan("fundamentals").select(*KEY, *fields), on=KEY, validate="1:1")
    factors = scan("adj_factor").sort(["code", "date"])
    stock = (
        stock.filter(pl.col("date") <= end)
        .sort(["code", "date"])
        .join_asof(factors, on="date", by="code", check_sortedness=False)
        .join(calendar.lazy(), on="date", validate="m:1")
        .sort(["code", "date"])
        .with_columns(
            adjusted=pl.col("close") * pl.col("hfq_factor"),
            opened=(~pl.col("is_limit_up")).fill_null(False).cum_max().over("code"),
        )
        .with_columns(
            ma20=pl.col("adjusted").rolling_mean(20).over("code"),
            ret=pl.col("adjusted").pct_change().over("code"),
            previous_session=pl.col("session").shift().over("code"),
        )
        .with_columns(
            ret=pl.when(pl.col("session") - pl.col("previous_session") == 1).then(pl.col("ret"))
        )
    )
    result = stock.collect()
    if result["hfq_factor"].null_count():
        raise ValueError("Missing effective adjustment factors")
    return result.drop("hfq_factor", "previous_session")


def market_width(panel, index):
    daily = panel.group_by("date").agg(pl.col("ret").mean().alias("all_a_return"))
    trend = (
        panel.filter(
            ~pl.col("is_st"),
            (pl.col("date") - pl.col("listing_date")).dt.total_days() > 180,
            pl.col("ma20").is_not_null(),
        )
        .group_by("date").agg((pl.col("adjusted") > pl.col("ma20")).mean().alias("ma20_breadth"))
    )
    industry = (
        panel.filter(pl.col("industry").is_not_null())
        .group_by("industry", "date").agg(pl.col("ret").mean().alias("ret"))
        .sort("industry", "date")
        .with_columns(growth=(pl.col("ret") + 1).log().rolling_sum(20).over("industry"))
        .group_by("date").agg((pl.col("growth") > 0).mean().alias("industry_breadth"))
    )
    return (
        index.join(daily, on="date", how="left").join(trend, on="date", how="left")
        .join(industry, on="date", how="left").sort("date")
        .with_columns(
            weight_breadth=(1 + pl.col("all_a_return")).log().rolling_sum(20).exp()
            - pl.col("csi300") / pl.col("csi300").shift(20)
        )
    )


def ranked_members(panel):
    return (
        panel.filter(
            ~pl.col("is_st"), pl.col("opened"), pl.col("total_market_cap") > 0,
            pl.col("delisting_date").is_null() | (pl.col("date") < pl.col("delisting_date")),
        )
        .select(*KEY, "total_market_cap").sort("date", "total_market_cap", "code")
        .with_columns(rank=pl.col("code").cum_count().over("date"))
        .filter(pl.col("rank") <= 800)
    )


def flow_members(ranked, calendar, frequency):
    interval = {"monthly": "1mo", "weekly": "1w"}[frequency]
    periods = calendar.with_columns(period=pl.col("date").dt.truncate(interval))
    # The previous period's final trading close supplies the current basket.
    snapshots = (
        periods.group_by("period").agg(pl.col("date").max().alias("date"))
        .sort("period").with_columns(period=pl.col("period").shift(-1))
    )
    return (
        ranked.filter(pl.col("rank") <= 400).join(snapshots, on="date")
        .select("period", "code").join(periods.select("date", "period"), on="period")
        .select(*KEY)
    )


def flow_signals(panel, ranked, calendar, frequency="monthly"):
    micro = flow_members(ranked, calendar, frequency).with_columns(group=pl.lit("micro"))
    csi = scan("csi300_members").select(*KEY).collect().with_columns(group=pl.lit("csi"))
    flow = scan("active_flow").select(*KEY, "active_buy", "active_sell")
    daily = (
        pl.concat([micro, csi]).lazy()
        .join(panel.lazy().select(*KEY, "amount"), on=KEY, how="left", validate="m:1")
        .join(flow, on=KEY, how="left", validate="m:1")
        .with_columns(net=(pl.col("active_buy").cast(pl.Float64) - pl.col("active_sell")) * 1000)
        .with_columns(valid=(pl.col("amount") > 0) & pl.col("net").is_not_null())
        .group_by("date", "group").agg(
            pl.col("net").filter(pl.col("valid")).sum(),
            pl.col("amount").filter(pl.col("valid")).sum(),
            pl.col("valid").fill_null(False).mean().alias("coverage"),
        )
        .collect()
    )
    out = calendar.select("date")
    for group in ("micro", "csi"):
        out = out.join(daily.filter(pl.col("group") == group).drop("group").rename({
            name: f"{group}_{name}" for name in ("net", "amount", "coverage")
        }), on="date", how="left")
    out = out.sort("date")
    for group in ("micro", "csi"):
        net = pl.col(f"{group}_net").rolling_sum(5)
        amount = pl.col(f"{group}_amount").rolling_sum(5)
        out = out.with_columns(pl.when(amount > 0).then(net / amount).alias(f"{group}_strength"))
    return (
        out.with_columns(spread=pl.col("micro_strength") - pl.col("csi_strength"))
        .with_columns(flow_change=pl.col("spread") - pl.col("spread").shift().rolling_mean(20, min_samples=10))
        .with_columns(
            flow_level=pl.col("micro_strength").ewm_mean(span=10, adjust=False),
            flow_change=pl.col("flow_change").ewm_mean(span=10, adjust=False),
        )
    )


def score(frame, years, minimum):
    width = ["ma20_breadth", "weight_breadth", "industry_breadth"]
    flow = ["flow_level", "flow_change"]
    columns = []
    for name in width + flow:
        # Lag the observation in each date window so the current value cannot rank itself.
        scores = []
        dates, values = frame["date"].to_list(), frame[name].to_list()
        lower = 0
        for i, (day, value) in enumerate(zip(dates, values)):
            boundary = day.replace(year=day.year - years, day=28 if (day.month, day.day) == (2, 29) else day.day)
            while dates[lower] < boundary:
                lower += 1
            history = [v for v in values[lower:i] if v is not None]
            if value is None or len(history) < minimum:
                scores.append(None)
            else:
                scores.append(sum((v < value) + 0.5 * (v == value) for v in history) / len(history))
        columns.append(pl.Series(f"{name}_pct", scores))
    return frame.with_columns(columns).with_columns(
        width_score=sum(pl.col(f"{name}_pct") for name in width) / 3,
        flow_score=sum(pl.col(f"{name}_pct") for name in flow) / 2,
    ).with_columns(combined_score=(pl.col("width_score") + pl.col("flow_score")) / 2)
