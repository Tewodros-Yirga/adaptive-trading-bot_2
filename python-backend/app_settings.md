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
| `{strategy}_backtest_symbols` | JSON list | `["XAUUSD","EURUSD"]` | Symbols used when running candidate backtests. |
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

## Strategy Picker

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `picker_max_simultaneous_strategies` | int | `1` | Maximum number of strategies selected per signal evaluation cycle. When set to 1, only the highest-scoring qualifying strategy trades. Set to 2+ for multi-strategy ensemble mode. |
| `picker_min_score` | float | `0.3` | Minimum picker score (0.0–1.0) a strategy must achieve to be selected for trading. Strategies scoring below this threshold are skipped even if they are the top scorer. |
| `picker_secondary_threshold` | float | `0.85` | When `picker_max_simultaneous_strategies > 1`, a secondary strategy is only selected if its score is at least this fraction of the top strategy's score. Prevents weak strategies from piggy-backing on a strong one. |
| `picker_lookback_trades` | int | `20` | Number of most recent closed trades per strategy used when computing the live performance scoring factors. |
| `picker_min_trades_for_scoring` | int | `5` | Minimum number of closed trades required before a strategy is eligible for live performance scoring. Below this, the strategy uses its backtest composite score only. |
| `picker_learning_rate` | float | `0.05` | Online learning rate for weight updates after each closed trade. A WIN nudges weights toward the selecting strategy; a LOSS nudges away. |
| `picker_recency_lambda` | float | `0.1` | Exponential decay constant for the `recency_of_last_win` scoring factor. Higher values penalise strategies that haven't won recently more aggressively. |
| `picker_news_bias_threshold` | float | `0.5` | Minimum absolute news sentiment score required to apply a news bonus or penalty to picker strategy scores. |
| `picker_news_bonus` | float | `0.15` | Score bonus applied to a strategy when its direction aligns with the current news sentiment and `|sentiment| ≥ picker_news_bias_threshold`. |
| `picker_news_penalty` | float | `0.15` | Score penalty applied to a strategy when its direction conflicts with the current news sentiment. |
| `picker_news_veto_threshold` | float | `0.85` | If `|news_sentiment| ≥ this value`, strategies signalling against the sentiment direction are hard-vetoed (score set to 0), regardless of other factors. |

### Picker Factor Weights

These seven weights determine how the overall picker score is computed. They are normalised to sum to 1.0 at read time, so they do not need to be exact.

| Key | Type | Default | Scoring factor |
|-----|------|---------|----------------|
| `picker_weight_recent_win_rate` | float | `0.25` | Win rate over the last `picker_lookback_trades` closed trades for this strategy. |
| `picker_weight_profit_factor` | float | `0.20` | Profit factor (gross profit ÷ gross loss) over recent closed trades. Capped at 5.0 before normalisation. |
| `picker_weight_backtest_composite_score` | float | `0.20` | Latest promoted candidate composite score from the continuous backtest engine. Falls back to 0.5 if no qualified candidate exists. |
| `picker_weight_drawdown` | float | `0.15` | Inverse of the recent maximum drawdown. A strategy with low drawdown scores higher. |
| `picker_weight_signal_confidence` | float | `0.10` | Strategy's own confidence value from the most recent signal for this symbol. |
| `picker_weight_recency_of_last_win` | float | `0.05` | Exponentially decayed time since the strategy's last winning trade. Recent wins score higher. |
| `picker_weight_parameter_freshness` | float | `0.05` | How recently the strategy's parameters were updated by the continuous backtest engine. Fresh parameters score higher. |

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

# Picker
picker_max_simultaneous_strategies, picker_min_score,
picker_secondary_threshold, picker_lookback_trades,
picker_min_trades_for_scoring, picker_learning_rate,
picker_recency_lambda, picker_news_bias_threshold, picker_news_bonus,
picker_news_penalty, picker_news_veto_threshold,
picker_weight_recent_win_rate, picker_weight_profit_factor,
picker_weight_backtest_composite_score, picker_weight_drawdown,
picker_weight_signal_confidence, picker_weight_recency_of_last_win,
picker_weight_parameter_freshness
```