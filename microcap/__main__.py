import argparse
from datetime import date, datetime
import json
from uuid import uuid4

import polars as pl

from .attribution import (
    monthly_stock_distribution, phase_risk, rebalance_attribution, simulated_returns, tracking,
)
from .backtest import backtest, performance
from .data import ROOT, audit, indices
from .gold import gold_allocation, gold_returns
from .signals import flow_signals, market_width, ranked_members, score, stock_panel


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("task", choices=["audit", "baseline", "run"])
    parser.add_argument("--flow-frequency", choices=["monthly", "weekly"], default="monthly")
    args = parser.parse_args()
    task = args.task
    config = json.loads((ROOT / "config/research.json").read_text(encoding="utf-8"))
    start = date.fromisoformat(config["start"])
    end = date.fromisoformat(config["end"])
    output = ROOT / "outputs" / f"{task}-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:8]}"
    tables = {}
    if task == "audit":
        tables["data_audit"] = audit()
    else:
        index = indices().filter(pl.col("date") <= end)
        if index["date"][-1] != end or start not in index["date"]:
            raise ValueError("Configured start/end must be covered trading days")
        if task == "baseline":
            tables["baseline"] = (
                index.with_columns(
                    micro_return=pl.col("micro").pct_change(),
                    csi300_return=pl.col("csi300").pct_change(),
                ).filter(pl.col("date") >= start)
                .with_columns(((pl.col("micro_return") + pl.col("csi300_return")) / 2).alias("benchmark_return"))
                .with_columns(
                    *[pl.when(pl.col("date") == start).then(0.0).otherwise(pl.col(f"{name}_return"))
                      .alias(f"{name}_return") for name in ("micro", "csi300", "benchmark")]
                )
                .with_columns(
                    *[(1 + pl.col(f"{name}_return")).cum_prod().alias(f"{name}_nav")
                      for name in ("micro", "csi300", "benchmark")]
                )
            )
        else:
            gold = gold_returns(index.filter(pl.col("date") >= start).select("date"))
            calendar = index.select("date").with_row_index("session")
            print("Loading adjusted stock panel", flush=True)
            panel = stock_panel(end, calendar)
            print("Building market width, baskets and fund-flow factors", flush=True)
            width = market_width(panel, index)
            ranked = ranked_members(panel)
            config["flow_frequency"] = args.flow_frequency
            flow = flow_signals(panel, ranked, calendar, args.flow_frequency)
            signals = score(width.join(flow, on="date"), config["rank_years"], config["rank_min_observations"])
            daily, metrics, annual = performance(backtest(signals, start), config["one_way_fee"])
            gold_daily, gold_metrics, gold_annual = gold_allocation(daily, gold, config["gold_weights"])
            index_start = date.fromisoformat(config["index_start"])
            simulated = simulated_returns(panel, ranked, calendar, index).filter(
                pl.col("date") >= index_start
            )
            print("Calculating tracking differences and return attribution", flush=True)
            tracking_outliers = simulated.with_columns(
                difference=pl.col("return_micro") - pl.col("wind_return")
            ).sort(pl.col("difference").abs(), descending=True).head(50)
            tables = {
                "gold_daily": gold_daily, "gold_metrics": gold_metrics, "gold_annual_returns": gold_annual,
                "tracking_outliers": tracking_outliers,
                "rebalance_attribution": rebalance_attribution(panel, ranked, calendar, index_start),
                "monthly_stock_distribution": monthly_stock_distribution(panel, ranked, calendar, index_start),
                "daily_backtest": daily, "metrics": metrics, "annual_returns": annual,
                "simulated_indices": simulated, "tracking": tracking(simulated),
                "phase_risk": phase_risk(width), "signals": signals,
                "daily_members": ranked,
            }
    output.mkdir(parents=True)
    for name, table in tables.items():
        if name == "daily_members":
            table.write_parquet(output / f"{name}.parquet")
        else:
            table.write_csv(output / f"{name}.csv")
    (output / "research.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
