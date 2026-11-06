from datetime import date, timedelta
import unittest

import polars as pl

from microcap.backtest import backtest, performance
from microcap.signals import flow_members, score


class BacktestTests(unittest.TestCase):
    def test_execution_close_and_next_day_exposure(self):
        start = date(2020, 1, 1)
        dates = [start + timedelta(days=i) for i in range(5)]
        data = pl.DataFrame({
            "date": dates,
            "micro": [100.0, 110.0, 121.0, 133.1, 146.41],
            "csi300": [100.0] * 5,
            **{f"{name}_score": [0.8, 0.5, 0.2, 0.2, 0.2] for name in ("width", "flow", "combined")},
        })
        daily, _, _ = performance(backtest(data, start), 0.001)
        self.assertEqual(daily["combined_close_weight"].to_list(), [0, 1, 1, 0, 0])
        self.assertEqual(daily["combined_return_weight"].to_list(), [0, 0, 1, 1, 0])
        self.assertAlmostEqual(daily["combined_return"][1], -0.002)
        self.assertAlmostEqual(daily["combined_return"][2], 0.10)
        self.assertAlmostEqual(daily["combined_return"][3], 1.10 * 0.998 - 1)
        self.assertAlmostEqual(daily["benchmark_nav"][-1], 1.05 ** 4)

    def test_rank_uses_only_prior_observations(self):
        data = pl.DataFrame({
            "date": [date(2020, 1, i) for i in range(1, 5)],
            **{name: [0.0, 2.0, 1.0, 100.0] for name in
               ("ma20_breadth", "weight_breadth", "industry_breadth", "flow_level", "flow_change")},
        })
        self.assertEqual(score(data, 5, 2)["combined_score"].to_list(), [None, None, 0.5, 1.0])


    def test_weekly_members_use_previous_traded_week_across_holiday(self):
        days = [date(2024, 2, 7), date(2024, 2, 8), date(2024, 2, 19), date(2024, 2, 20)]
        calendar = pl.DataFrame({"date": days})
        ranked = pl.DataFrame({
            "date": days, "code": ["old", "last_close", "new", "new"], "rank": [1, 1, 1, 1],
        })
        members = flow_members(ranked, calendar, "weekly").sort("date")
        self.assertEqual(members.rows(), [(days[2], "last_close"), (days[3], "last_close")])
