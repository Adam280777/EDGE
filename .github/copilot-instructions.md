# EDGE — Project-wide Copilot Instructions

## 1. Project identity

EDGE stands for Equity Discovery & Growth Engine.

EDGE is a research, stock discovery, portfolio analysis and trading decision-support platform for a UK-based retail investor.

Its purpose is to identify potentially attractive companies, analyse investment opportunities, create structured trade plans, monitor existing positions and help the user make better-informed decisions.

EDGE is a decision-support system. It is not an autonomous trading bot.

## 2. Core objectives

Build a reliable application that can:

1. Discover promising companies across relevant equity markets.
2. Identify emerging growth, overlooked companies and asymmetric opportunities.
3. Analyse company fundamentals, valuation, technicals, news and catalysts.
4. Rank opportunities using transparent, explainable criteria.
5. Produce separate swing-trading and long-term investment research.
6. Create structured trade plans with entries, stops, targets and position sizing.
7. Monitor manually entered positions and flag meaningful changes.
8. Track portfolio performance, profit/loss, exposure and risk.
9. Maintain a research journal and record investment decisions.
10. Evaluate strategies through realistic historical testing.

## 3. Two distinct investment engines

### Swing Trading Engine

Designed for trades lasting approximately 2 days to 8 weeks.

Analyse:
- Price action and technical trends.
- Momentum, relative volume and liquidity.
- Support and resistance.
- Volatility and potential entry zones.
- Upcoming earnings and company catalysts.
- Short-term fundamental developments.
- Risk/reward and invalidation levels.

Every proposed trade should include:
- Ticker and company name.
- Research date and data timestamps.
- Trade thesis.
- Entry zone and entry conditions.
- Stop-loss and reason for invalidation.
- Target levels.
- Reward-to-risk ratio.
- Position size and maximum cash risk.
- Expected holding period.
- Upcoming catalysts.
- Bull and bear cases.
- Reasons not to take the trade.

### Long-Term Investment Engine

Designed for investments held approximately 6 months to 2 years or longer.

Analyse:
- Revenue and earnings growth.
- Gross, operating and net margins.
- Cash flow and balance sheet.
- Debt, dilution and share count.
- Competitive advantage.
- Management and capital allocation.
- Market size and growth opportunities.
- Valuation and expected return scenarios.
- Long-term risks and thesis deterioration.

Do not mix swing-trading signals with long-term investment recommendations.

## 4. Data integrity

Financial accuracy is more important than producing impressive-looking results.

- Never invent or fabricate prices, financial figures, news or company information.
- Record the source and timestamp of important data.
- Distinguish live, delayed and historical data.
- Clearly identify missing, stale or incomplete data.
- Do not silently replace missing values with estimates.
- Validate external data before using it in calculations.
- Handle stock splits, dividends, currency conversions and corporate actions where relevant.
- Avoid duplicate records and inconsistent ticker/company mappings.
- Cache data responsibly and respect API limits and licences.
- Prefer free or affordable data sources where practical.

If a data source is unavailable, explain the limitation rather than pretending the information is current.

## 5. Financial calculations and risk management

All important financial calculations must use deterministic, testable code.

AI-generated text must never be the source of truth for:
- Position sizing.
- Portfolio balances.
- Profit and loss.
- Currency conversion.
- Risk per trade.
- Stop-loss calculations.
- Portfolio exposure.
- Reward-to-risk ratios.

Use decimal-safe financial calculations where appropriate.

Position sizing must account for:
- Account equity.
- Maximum permitted cash risk.
- Entry price.
- Stop price.
- Currency conversion.
- Fees, spread and slippage where available.
- Whole-share constraints where relevant.

Never recommend a position that exceeds the configured risk limit.

Use conservative defaults and clearly identify assumptions.

## 6. Research and AI behaviour

AI should explain and challenge investment ideas, not simply generate buy signals.

For every serious investment thesis:
- Present the strongest supporting evidence.
- Present the strongest opposing evidence.
- Identify what the market may already have priced in.
- Identify upcoming catalysts.
- Identify possible dilution, debt and liquidity concerns.
- Explain what would invalidate the thesis.
- Separate facts from estimates and opinions.
- State uncertainty clearly.

Never guarantee returns or imply that a forecast is certain.

Do not treat a high growth rate or a low valuation multiple as sufficient reason to buy.

## 7. Position monitoring

Positions are entered manually by the user after execution through their broker.

EDGE may monitor:
- Price movement.
- Stop and target proximity.
- Technical deterioration.
- Earnings and corporate announcements.
- Changes in fundamentals.
- New risks and catalysts.
- Whether the original thesis remains valid.

Use clear statuses:
- Healthy.
- Improving.
- Caution.
- Deteriorating.
- Thesis invalidated.

Explain why a status changes.

Never silently change a user's stop-loss, target or position size.

Never execute broker orders or make live trades without explicit future authorisation and a separately reviewed implementation.

## 8. Software engineering standards

- Inspect the existing codebase before changing it.
- Preserve working functionality.
- Prefer modular, maintainable and well-tested code.
- Use type hints and clear naming.
- Avoid unnecessary dependencies.
- Keep financial calculations separate from presentation and AI reasoning.
- Use configuration files and environment variables appropriately.
- Never hardcode API keys or credentials.
- Handle errors explicitly.
- Add tests for important new functionality.
- Do not claim tests passed unless they were actually run.
- Avoid large rewrites unless specifically justified.

## 9. Development workflow

For significant tasks:

1. Understand the user's objective.
2. Inspect relevant files and existing architecture.
3. Identify dependencies and potential risks.
4. Propose a clear implementation plan.
5. Make focused changes.
6. Run relevant tests.
7. Review the changes for regressions.
8. Explain what changed and what remains incomplete.

Ask before destructive changes, database resets, major migrations or changes that could lose user data.

Use Git checkpoints before substantial work where Git is configured.

## 10. Technology and cost principles

Start with a straightforward, affordable architecture.

Prefer Python for research, data processing and financial calculations.

Use SQLite initially if appropriate.

Use a simple dashboard framework such as Streamlit for an early prototype unless the existing project already has a better foundation.

Avoid paid services until a real need is demonstrated.

Keep external data providers replaceable so EDGE does not become dependent on one provider.

Do not add a technology just because it is popular.

## 11. User experience

EDGE should be:
- Professional and visually polished.
- Easy to navigate.
- Clear rather than cluttered.
- Evidence-led.
- Consistent across screens.
- Transparent about data freshness and uncertainty.

Prioritise useful dashboards, understandable charts, clear tables and actionable research summaries.

Do not fill the interface with meaningless scores, fake data or decorative metrics.

## 12. Important boundaries

- No automatic live trading.
- No fabricated data.
- No untested financial calculations.
- No hidden changes to risk settings.
- No unnecessary paid APIs.
- No destructive database operations without approval.
- No claims of guaranteed investment performance.

The overriding priority is to make EDGE accurate, trustworthy, explainable and maintainable.
