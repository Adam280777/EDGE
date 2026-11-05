---
name: strategy-backtesting
description: "Use to evaluate trading strategies with realistic historical simulations, bias controls, costs, out-of-sample testing, and performance reporting."
---

# EDGE Strategy Backtesting

Evaluate trading strategies using realistic historical simulations.

Requirements:
- Define the strategy rules before testing.
- Avoid look-ahead bias.
- Avoid survivorship bias where possible.
- Use point-in-time information where available.
- Include spreads, commissions and slippage.
- Separate training, validation and out-of-sample periods.
- Use walk-forward testing where appropriate.
- Record all assumptions and limitations.

Report:
- Total return.
- Annualised return where appropriate.
- Maximum drawdown.
- Win rate.
- Average win and loss.
- Profit factor.
- Expectancy.
- Risk-adjusted performance.
- Number of trades.
- Benchmark comparison.

Never present backtested results as guaranteed future performance.