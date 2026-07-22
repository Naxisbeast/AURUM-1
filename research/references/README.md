# Research References

Key papers that influenced AURUM's methodology.

## Deflated Sharpe Ratio

**Bailey, D. H., & López de Prado, M. (2014).** *The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting, and Non-Normality.* Journal of Portfolio Management, 40(5), 94-107.

Corrects the Sharpe ratio for:
1. Multiple testing — if you test enough variants, one will look great by chance
2. Non-normality — skew and fat tails distort standard Sharpe significance tests

Used by AURUM: `aurum1/research/deflated_sharpe.py` and `trial_ledger.py`

**López de Prado, M. (2018).** *Advances in Financial Machine Learning.* Wiley.

Textbook covering DSR, combinatorially symmetric cross-validation, and ensemble methods for financial time series.

## Walk-Forward Analysis

Walk-forward is the primary validation methodology used by AURUM. The 18-window, 11-year protocol with non-overlapping test periods follows the standard described in:

- Pardo, R. (2011). *The Evaluation and Optimization of Trading Strategies.* Wiley.

## Stationarity Testing

Augmented Dickey-Fuller tests (implemented in `scripts/audit/stationarity.py`) follow:

- Dickey, D. A., & Fuller, W. A. (1979). *Distribution of the Estimators for Autoregressive Time Series with a Unit Root.* Journal of the American Statistical Association, 74(366), 427-431.

## Transaction Cost Analysis

- Almgren, R., & Chriss, N. (2001). *Optimal Execution of Portfolio Transactions.* Journal of Risk, 3(2), 5-39.

The folded-normal slippage model used in AURUM's PaperBroker is inspired by the Almgren-Chriss market impact framework, adapted for the simplified case of a single-instrument breakout system.
