from datetime import date
import unittest

import polars as pl

from microcap.gold import gold_allocation


class GoldTests(unittest.TestCase):
    def test_same_gold_weight_in_strategy_and_benchmark(self):
        dates = [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)]
        core = pl.DataFrame({
            "date": dates, "combined_return": [0.0, 0.1, -0.1], "benchmark_return": [0.0, 0.05, 0.0],
        })
        gold = pl.DataFrame({"date": dates, "gold_return": [0.0, -0.05, 0.1]})
        daily, _, annual = gold_allocation(core, gold, [0.4])
        self.assertAlmostEqual(daily["strategy_return"][1], 0.04)
        self.assertAlmostEqual(daily["benchmark_return"][1], 0.01)
        self.assertAlmostEqual(daily["relative_nav"][-1], 1.04 * 0.98 / (1.01 * 1.04))
        self.assertAlmostEqual(annual["strategy"][0], 1.04 * 0.98 - 1)
