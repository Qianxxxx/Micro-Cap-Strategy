# 微盘股研报复现

复现兴业证券《如何把握微盘的配置窗口？——历史复盘、收益分解与双维度择时》的微盘模拟及轮动策略。择时回测为 2013-12-31 至 2026-04-30，微盘指数模拟从 2010-01-04 开始。数据预热从现有历史起点开始。

[研报节选](https://mp.weixin.qq.com/s/Z4QfdpHmtBKawi9DKM5mDw) · [公式与口径](docs/methodology.md)

## 目录

| 目录 | 用途 |
|---|---|
| `data/` | 现有原始 Parquet 和数据说明，研究程序只读 |
| `config/` | 样本和回测参数、MySQL 配置模板、本机连接配置 |
| `microcap/` | 数据接入、信号、组合回测、归因和命令入口 |
| `tests/` | 执行时序、费率、分位数回归校验 |
| `docs/` | 研报口径和复现边界 |
| `outputs/` | 每次运行独立落盘，含参数快照和可审计明细 |

## 运行

在项目根目录使用 Python 3.10 或更新版本运行。

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m unittest discover -s tests -v
.\.venv\Scripts\python -m microcap audit
.\.venv\Scripts\python -m microcap baseline
.\.venv\Scripts\python -m microcap run
.\.venv\Scripts\python -m microcap run --flow-frequency weekly
```

`audit` 使用 `config/mysql.local.json` 对比八张业务表与 Parquet 的行数和日期范围，并验证两张回测视图可读。用 `mysql.example.json` 创建本机配置。真实配置、原始数据、环境和输出均在 Git 忽略规则中。批量回测直接扫描本地 Parquet，便于复跑和减少 MySQL 传输。

`baseline` 输出两个指数和 50/50 基准净值。`run` 另输出五项信号、三条轮动策略、日度仓位与费用、绩效指标、年度回报、逐日候选成分、指数跟踪和分阶段风险。各次运行按输出目录区分。黄金数据来自 `data/index/AU9999.SGE.parquet`，`run` 同时生成各档黄金权重的日度净值、绩效和年度收益。


## 保留的结果与复现边界

- 月度研报复现与周度对照均包含权益择时、收益归因和黄金配置。默认命令使用月度，指定 `--flow-frequency weekly` 运行周度。
- [验收记录](docs/validation.md)供结果复核。运行前需自行准备符合代码所列目录及字段的 Parquet 文件。
- 优先排查微盘模拟指数的跟踪误差。当前年化跟踪误差为 3.48%，研报为 0.44%，统计区间不同。可从偏差最大的 2015-07-10 入手核对成分资格、停复牌和收益处理。偏差也会传导到使用模拟成分的资金流信号和收益归因。
- 若要求逐表严格复现，还需历史退市风险警示、停复牌、时点申万行业及中证800逐日成分。公开节选未明确的参数和执行约定见[实现口径](docs/methodology.md)。现有回测截止日为 2026-04-30。
