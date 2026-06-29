"""
strategies.challenge
---------------------
XAUUSD H4 trend-following edge for passing a funded/prop challenge
(FundingPips $5k 2-step). Donchian(20) breakout with EMA20/50 bias and a
3xATR chandelier trailing stop; risk-based position sizing and a hard
drawdown / kill-switch guard.

See the `xau-challenge-doctrine` skill for the why: a high win rate is not an
edge — this module trades a positive-expectancy trend-follow with the hard stop
ALWAYS on and worst case capped near -1R.
"""
