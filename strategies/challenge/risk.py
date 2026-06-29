"""
risk.py
-------
Risk-based position sizing and the challenge guardrail.

`position_size` sizes lots from a fixed-fractional risk budget, never from a lot
target — a -1R loss stays inside the daily/overall limits. `ChallengeGuard`
enforces the FundingPips $5k 2-step rails: daily & overall drawdown, a
consecutive-loss kill-switch, and the profit target (stop trading once passed).
"""
from __future__ import annotations

import math


# XAUUSD contract: 1.00 lot = 100 oz, so a $1 move = $100 per lot.
XAU_CONTRACT_USD_PER_DOLLAR_PER_LOT = 100.0


def position_size(equity: float, risk_pct: float, sl_distance: float, *,
                  contract_size: float = XAU_CONTRACT_USD_PER_DOLLAR_PER_LOT,
                  lot_step: float = 0.01, min_lot: float = 0.01,
                  max_lot: float | None = None) -> float:
    """Lots to risk exactly `risk_pct` of `equity` over a `sl_distance` ($) stop.

      risk_$   = equity * risk_pct
      raw_lots = risk_$ / (sl_distance * contract_size)

    Rounded DOWN to `lot_step` so realized risk never exceeds the budget. Returns
    0.0 when the stop distance is non-positive or when even `min_lot` would risk
    more than the budget (refuse rather than over-risk). Caps at `max_lot`.
    """
    if sl_distance <= 0 or risk_pct <= 0 or equity <= 0:
        return 0.0
    risk_amount = equity * risk_pct
    raw = risk_amount / (sl_distance * contract_size)
    lots = math.floor(raw / lot_step + 1e-9) * lot_step
    if lots < min_lot:
        return 0.0
    if max_lot is not None:
        lots = min(lots, max_lot)
    return round(lots, 2)


class ChallengeGuard:
    """Drawdown / kill-switch / target gate for a single challenge account.

    Equity is measured against the account's INITIAL balance for the overall
    floor and the profit target; the daily drawdown is measured against the
    equity at the start of the current trading day (set via `start_new_day`).
    `record_trade` tracks realized PnL for the day and the consecutive-loss
    streak. `can_trade` returns (allowed, reason).
    """

    def __init__(self, initial_balance: float, *, profit_target_pct: float,
                 max_daily_dd_pct: float, max_overall_dd_pct: float,
                 max_consec_losses: int):
        self.initial_balance = float(initial_balance)
        self.profit_target_pct = float(profit_target_pct)
        self.max_daily_dd_pct = float(max_daily_dd_pct)
        self.max_overall_dd_pct = float(max_overall_dd_pct)
        self.max_consec_losses = int(max_consec_losses)
        self.day_start_equity: float | None = None
        self.day_realized: float = 0.0
        self.consec_losses: int = 0

    # ── derived levels ────────────────────────────────────────────────────────
    @property
    def overall_floor(self) -> float:
        return self.initial_balance * (1.0 - self.max_overall_dd_pct)

    @property
    def target_equity(self) -> float:
        return self.initial_balance * (1.0 + self.profit_target_pct)

    @property
    def daily_loss_limit(self) -> float:
        base = self.day_start_equity if self.day_start_equity is not None else self.initial_balance
        return base * self.max_daily_dd_pct

    # ── lifecycle ─────────────────────────────────────────────────────────────
    def start_new_day(self, equity: float) -> None:
        """Reset the daily budget and the loss streak at the start of a trading day."""
        self.day_start_equity = float(equity)
        self.day_realized = 0.0
        self.consec_losses = 0

    def record_trade(self, pnl: float) -> None:
        """Account a closed trade. A loss extends the streak; a win clears it; a
        scratch (0) leaves it unchanged."""
        self.day_realized += float(pnl)
        if pnl < 0:
            self.consec_losses += 1
        elif pnl > 0:
            self.consec_losses = 0

    def can_trade(self, equity: float) -> tuple[bool, str]:
        """Return (allowed, reason). Reasons: 'ok', 'target_reached', 'overall_dd',
        'daily_dd', 'consec_losses'."""
        if equity >= self.target_equity:
            return False, "target_reached"
        if equity <= self.overall_floor:
            return False, "overall_dd"
        if self.day_realized <= -self.daily_loss_limit:
            return False, "daily_dd"
        if self.consec_losses >= self.max_consec_losses:
            return False, "consec_losses"
        return True, "ok"
