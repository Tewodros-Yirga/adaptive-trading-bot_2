# AppSettings Reference

All configurable values are stored as key-value pairs in the `app_settings` database table and are read at runtime — no restart required for most settings. They can be read and written from the Settings page in the frontend or via the REST API (`GET/PUT /settings`).

All values are stored as strings. Type annotations below show how the value is interpreted by the application.

---

## Risk Management

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `account_balance` | float | `10000.0` | Account balance used for lot size calculations. Updated automatically when the bridge syncs, or can be set manually. |
| `leverage` | int | `100` | Leverage multiplier applied by the broker (e.g. 100 = 100:1). Used to compute margin and lot sizes. |
| `risk_per_trade_pct` | float | `1.0` | Maximum percentage of account balance risked per trade. Lot size is calculated so that a full stop-loss hit equals this loss. |
| `max_open_trades` | int | `5` | Maximum number of trades that may be open simultaneously across all strategies. New signals are blocked once this limit is reached. |
| `max_daily_loss_pct` | float | `5.0` | If total realised PnL for the current UTC day falls below `-(account_balance × max_daily_loss_pct / 100)`, all new trades are blocked for the remainder of the day. |
| `max_drawdown_pct` | float | `20.0` | If the current drawdown from the equity peak exceeds this percentage, a trading halt is triggered automatically. |
| `lot_size_mode` | str | `FIXED` | `FIXED` — uses a fixed lot size per trade. `DYNAMIC` — calculates lot size based on `risk_per_trade_pct` and ATR-based stop distance. |
| `trading_halt` | bool | `false` | When `true`, all new trade entries are blocked. Set automatically by the drawdown guard, or manually via the dashboard. |
| `symbol_exposure_limit` | float | `1.0` | Maximum total lot size open on any single symbol at one time. Prevents over-exposure on correlated instruments. |

---

## News Intelligence

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `newsapi_key` | str | `""` | API key for [NewsAPI.org](https://newsapi.org). Used to fetch headlines for sentiment scoring. Leave empty to disable this source. |
| `alphavantage_key` | str | `""` | API key for [Alpha Vantage](https://www.alphavantage.co). Used as OHLCV fallback source 2 and for news. |
| `finnhub_key` | str | `""` | API key for [Finnhub](https://finnhub.io). Used for news and OHLCV data. |
| `anthropic_api_key` | str | `""` | Anthropic Claude API key. Required for AI-powered news sentiment scoring, global market context generation, and strategy pair analysis narratives. |
| `twelve_data_key` | str | `""` | API key for [Twelve Data](https://twelvedata.com). Used as OHLCV fallback source 3. |
| `news_lookback_hours` | int | `4` | How many hours of news history to consider when computing the news sentiment bias for a signal. |
| `news_block_threshold` | float | `0.7` | If the absolute value of the aggregated news sentiment for the symbol exceeds this threshold, the trade is blocked regardless of direction. |
| `news_caution_factor` | float | `0.5` | When sentiment is directionally aligned but below `news_block_threshold`, the strategy confidence is multiplied by `(1 - news_caution_factor × |sentiment|)` to reduce position sizing. |
| `retrospective_learning_interval_hours` | int | `4` | How often (in hours) the retrospective learning job runs to update `impact_learning_weight` on historical news items based on actual market moves. |
| `global_context_interval_minutes` | int | `30` | How often (in minutes) the global market context is regenerated via Claude. |
| `news_analysis_system_prompt` | str | `""` | Override the built-in Claude system prompt used for news sentiment analysis. Leave empty to use the default prompt. The prompt evolves automatically as the system learns. |
| `global_market_context` | JSON str | `{}` | Latest Claude-generated global market context summary. Written by the system; read by the orchestrator for signal enhancement. Shape: `{"summary": str, "bias": str, "risk_level": str, "updated_at": str}`. |

---

## Strategy Search & Continuous Backtesting

Settings follow the pattern `{strategy_name}_{suffix}`, where `{strategy_name}` is the exact name from the strategy registry (e.g. `DTC`, `Alchemist`, `RSI_Reversal`).

| Key (pattern) | Type | Default | Description |
|---------------|------|---------|-------------|
| `{strategy}_qualify_threshold_win_rate` | float | `55.0` | Minimum win rate (%) a backtest candidate must achieve to be promoted to qualified status. |
| `{strategy}_score_weight_win_rate` | float | `0.6` | Weight of win rate in the candidate composite score. Must be between 0.0 and 1.0. |
| `{strategy}_score_weight_roi` | float | `0.4` | Weight of net ROI in the candidate composite score. `score_weight_win_rate + score_weight_roi` should equal 1.0. |
| `{strategy}_backtest_interval_seconds` | int | `300` | How often (in seconds) the continuous backtest engine evaluates a new parameter candidate for this strategy. |
| `{strategy}_backtest_timeframes` | JSON list | `["1h","4h","1d"]` | Timeframes used when running candidate backtests. Each candidate is evaluated against all listed timeframes. |
| `{strategy}_backtest_symbols` | JSON list | `["XAUUSD"]` | Symbols used when running candidate backtests. |
| `{strategy}_param_step_size` | float | `0.05` | Fractional step size used by the parameter search engine when perturbing numeric parameters. A value of `0.05` means each parameter is changed by ±5% of its allowed range per search step. |
| `{strategy}_range_expansion_months` | int | `6` | When a candidate scores well enough, the backtest date range is extended by this many months to validate on out-of-sample data. |
| `{strategy}_max_history_months` | int | `36` | Maximum historical look-back in months for backtest data fetching. Older data is discarded to keep memory usage bounded. |

**Active strategy names as of migration 009:** `DTC`, `RSI_Reversal`, `MACD_Momentum`, `Bollinger_Breakout`, `Multi_EMA_Scalper`, `VWAP_Reversion`, `Alchemist`.

---

## Backtest Engine (Global)

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `backtest_adapt_every_n_trades` | int | `20` | During a backtest simulation, the parameter adaptation routine is triggered every N trades to simulate in-backtest optimisation. Smaller values adapt more frequently but may overfit. |

---

## News Veto

> **The strategy picker has been removed.** The `EnsembleVoter` is now the sole authority
> on trade direction (see `strategy/ensemble/voter.py`). The only surviving piece of the
> old picker is the news veto below. All former `picker_*` keys — `picker_min_score`,
> `picker_max_simultaneous_strategies`, `picker_secondary_threshold`, `picker_lookback_trades`,
> `picker_min_trades_for_scoring`, `picker_learning_rate`, `picker_recency_lambda`,
> `picker_news_bonus`, `picker_news_penalty`, and the eight `picker_weight_*` factor
> weights — are **obsolete and ignored**. If present in `app_settings`, they are dead rows.

The news veto runs **before** the ensemble vote and can block a trade outright. It only
fires when the news signal is both strong and highly credible **and** opposes *every*
strategy that is signalling on the current bar (`services/news_veto.py`). There is no
score adjustment — it is a clean block/allow.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `news_veto_bias_threshold` | float | `0.5` | Minimum absolute news sentiment magnitude before a veto is even considered. |
| `news_veto_threshold` | float | `0.85` | Minimum news confidence. When `|sentiment| ≥ news_veto_bias_threshold` **and** confidence `≥ this value`, a trade is vetoed if every signalling strategy points against the news direction. Raise → fewer vetoes. |

---

## Live Score Feedback

On every closed trade, each strategy that voted is given a `live_score` update — an EWMA
of the trade's realised **R-multiple** (reward earned per unit of risk), signed by whether
the strategy voted *with* or *against* the trade direction. The `EnsembleVoter` then scales
each backtest-derived weight by `(1 + score_feedback_weight_gain × live_score)`, floored at
`score_feedback_weight_floor`. This tilts the ensemble toward strategies that are profitable
*right now* without touching the backtest `composite_score`. See `services/score_feedback.py`.

`live_score` is stored per-strategy and **resets to 0 when a new parameter set is promoted**
for that strategy (the old R-multiples were earned by parameters that are no longer live).

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `score_feedback_enabled` | bool | `true` | Master on/off. When `false`, the voter uses raw backtest weights with no live tilt. |
| `score_feedback_alpha` | float | `0.2` | EWMA smoothing factor applied per closed trade. Higher reacts faster to recent trades (noisier); lower is smoother/slower. |
| `score_feedback_score_bound` | float | `3.0` | Symmetric clamp on the accumulated `live_score` (`[-bound, +bound]`) so a single streak can't dominate. |
| `score_feedback_weight_gain` | float | `0.25` | How hard `live_score` tilts the vote weight. At gain `0.25`, `live_score = +2` ≈ ×1.5 weight, `−2` ≈ ×0.5. |
| `score_feedback_weight_floor` | float | `0.1` | Minimum weight multiplier — a struggling strategy is dampened, never fully zeroed (the `WeightManager` suspension handles full removal). |

---

## System / Internal

These keys are written by the system and should not normally be edited manually.

| Key | Type | Description |
|-----|------|-------------|
| `global_market_context` | JSON str | Auto-updated by the global context loop. Contains Claude's current market summary and directional bias. |
| `news_analysis_system_prompt` | str | Evolved Claude system prompt for news analysis. Auto-updated by the retrospective learning loop. Leave empty to use the hardcoded default. |
| `trading_halt` | bool str | Set to `true` by the drawdown guard when `max_drawdown_pct` is breached. Can be manually reset to `false` from the dashboard. |

---

## Quick Reference: All Keys

```
# Risk
account_balance, leverage, risk_per_trade_pct, max_open_trades,
max_daily_loss_pct, max_drawdown_pct, lot_size_mode, trading_halt,
symbol_exposure_limit

# News
newsapi_key, alphavantage_key, finnhub_key, anthropic_api_key,
twelve_data_key, news_lookback_hours, news_block_threshold,
news_caution_factor, retrospective_learning_interval_hours,
global_context_interval_minutes, news_analysis_system_prompt,
global_market_context

# Backtest global
backtest_adapt_every_n_trades

# Per-strategy (replace {strategy} with e.g. DTC, Alchemist, RSI_Reversal, ...)
{strategy}_qualify_threshold_win_rate, {strategy}_score_weight_win_rate,
{strategy}_score_weight_roi, {strategy}_backtest_interval_seconds,
{strategy}_backtest_timeframes, {strategy}_backtest_symbols,
{strategy}_param_step_size, {strategy}_range_expansion_months,
{strategy}_max_history_months

# News veto (the only surviving piece of the old strategy picker)
news_veto_bias_threshold, news_veto_threshold

# Live score feedback
score_feedback_enabled, score_feedback_alpha, score_feedback_score_bound,
score_feedback_weight_gain, score_feedback_weight_floor
```