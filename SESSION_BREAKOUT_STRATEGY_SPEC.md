# SESSION_BREAKOUT — Bias-Filtered Opening-Range Breakout (XAUUSD, M5)

**End-to-end build & port spec.** Hand this to the Kronos ("chrono") engine to implement
`SESSION_BREAKOUT` as a new strategy, or to modify the existing challenge-execution path to run
this instead of the H4 trend-follow.

- **Instrument:** XAUUSD
- **Working timeframe:** M5 (5-minute closed bars)
- **Style:** intraday opening-range breakout, higher-TF bias-gated, one entry per session window
- **Frequency:** ~4.1 trades/day
- **Validation:** 3-year M5 backtest (246k bars, 2023-01 → 2026-06), train/test OOS split, cost + parameter stress
- **Execution class:** TAKER-compatible (pays the spread and still profits) → fits FundingPips acct `6c7ce166`
- **Source of truth (research code):** `s5_intraday_research.py` (loader, cost model, `strat_orb`), `s5_intraday_research2.py` (`bias_long_short`, `strat_orb_biased` — THE strategy), `s5_intraday_research5.py` (cost/param/weekly stress).
- **Template files to mirror for the port:** `E:\Projects\Kronos\KronosStrategies\strategies\backtest_strategies\kronos_challenge_xau.py` (Kronos strategy interface) and `bot/challenge_xau.py` (clean `signal()`/`position_size()` module).

> **Status honesty:** As of 2026-07-01 this strategy exists **only as research code**. There is no
> `bot/session_breakout.py` and it is **not** in the Kronos engine (Kronos runs `CHALLENGE_XAU`, the
> H4 trend-follow — a *different* strategy). This document is the missing bridge.

---

## 1. What it is and why it has an edge

At each of five fixed session-open hours, define the **first 30 minutes** as an *opening range* (OR).
Then take a **stop-entry breakout** of that range — **but only in the direction of a slow higher-TF
bias** (an EMA(240) trend proxy on M5). Stop = the opposite side of the OR; target = a multiple of the
OR width; flat by a 3-hour time stop or end of day.

The edge is **not** the breakout — a bare ORB is fragile (PF ~1.03, one good year). The edge is the
**bias filter**: it discards the counter-trend breaks that fail, keeping the trend-aligned breaks that
run. This is the same trend-aligned-breakout DNA as `bot/challenge_xau.py` (Donchian + EMA bias),
expressed intraday across multiple sessions instead of once on H4 — which is how it reaches ~4/day
while staying profitable, where every high-frequency *mean-reversion* fade in this project died at the
spread. See `MEANREV_SESSION_HANDOFF.md` and `research/FINDINGS.md` for the family that failed and why
this one didn't.

---

## 2. Data & environment requirements

| Requirement | Value |
|---|---|
| Bars | XAUUSD M5 OHLC, UTC timestamps, midnight-aligned |
| History needed before first signal | ≥ `n_long + slope_lk` = **288** closed M5 bars (~1 trading day) for the bias to warm up; give ≥ 2 days for safety |
| Signal cadence | Evaluate on the **last CLOSED** M5 bar. Never use the still-forming bar (strictly causal). |
| Session clock | UTC hour/minute of the bar timestamp |

The backtest data file is `reports/xau_m5_3y.csv` with header `time,o,h,l,c` (`time` = `%Y-%m-%d %H:%M:%S`).

---

## 3. Exact signal logic (the spec)

All rules below are transcribed verbatim from `strat_orb_biased` / `bias_long_short` in
`s5_intraday_research2.py`. Points = XAUUSD price points; at 0.10 lot, 1 point = $10.

### 3.1 Higher-TF bias filter — `bias(i) ∈ {+1, -1, 0}`

```
e   = EMA(close, n_long=240)              # ~20 hours on M5
up  = close[i] > e[i]  AND  e[i] > e[i - slope_lk]      # slope_lk = 48 bars (~4h)
dn  = close[i] < e[i]  AND  e[i] < e[i - slope_lk]
bias[i] = +1 if up else (-1 if dn else 0)
# undefined (skip) until i >= n_long + slope_lk = 288
```

Only `+1` permits longs, only `-1` permits shorts, `0` blocks all entries.

### 3.2 Session windows & opening range

- **Session-open hours (UTC):** `[1, 7, 12, 13, 14]` — validated as robust in BOTH train and test halves. These cover London (h1–7 area) and NY (h12–14). Dead hours (8–11 lunch, 21–22 rollover) are correctly excluded.
- For each session hour `sh` on each day, the **opening range** is the set of M5 bars with `bar.hour == sh` and `bar.minute < or_min` (`or_min = 30`) → the bars at `sh:00, sh:05, sh:10, sh:15, sh:20, sh:25` (up to 6 bars). Require **≥ 2 bars** present.
- `rng_hi = max(high)` over OR bars; `rng_lo = min(low)` over OR bars; `rng = rng_hi - rng_lo`. Skip if `rng <= 0`.

> Note: the five hours produce five *independent* OR windows per day. Hours 12,13,14 are consecutive, so
> the NY block effectively re-arms the breakout each hour — this is intentional and is where much of the
> ~4/day frequency comes from.

### 3.3 Entry rule (one per session)

Scan forward from the bar **after** the OR window, for up to `hold_bars = 36` M5 bars (3 hours), same
day only. Take the **first** bar that satisfies a bias-aligned break:

```
if high[k] >= rng_hi and bias[k] == +1:   # long breakout, uptrend
      side = long;  entry = rng_hi;  stop = rng_lo;  tp = rng_hi + tp_mult*rng
elif low[k]  <= rng_lo and bias[k] == -1:  # short breakout, downtrend
      side = short; entry = rng_lo;  stop = rng_hi;  tp = rng_lo - tp_mult*rng
```

- A break against the bias (or while bias == 0) is **ignored** — no trade that session.
- Entry price is the **range boundary** (`rng_hi`/`rng_lo`) — i.e. a **stop order at the level**, not a market order at the bar close. (Execution nuance for live/taker: see §8.3.)
- One position per session window; once entered, that session is done.

### 3.4 Exit rules

From the bar after entry, up to `hold_bars = 36` bars after entry, same day:

| Trigger | Exit price |
|---|---|
| Long: `low[k] <= stop` | `stop` (opposite OR side) |
| Long: `high[k] >= tp` | `tp` |
| Short: `high[k] >= stop` | `stop` |
| Short: `low[k] <= tp` | `tp` |
| New day reached before exit | previous bar close (flat by EOD) |
| `hold_bars` elapsed with no hit | close at bar `entry_k + hold_bars` (time stop) |

Stop and TP are **static** (no trailing). `tp_mult = 1.5` → target is 1.5× the OR width; stop is the
full OR width on the other side (≈ 1 : 1.5 risk:reward before costs, plus the time/EOD stop).

### 3.5 Position concurrency

At most **one open position at a time** per the backtest (sessions are scanned sequentially and each
resolves before the next). For live, cap `max_concurrent_positions = 1`.

---

## 4. Parameters (defaults + validated plateau)

| Param | Default | Validated plateau (holds PF ~1.3–1.46, ~66–70% green) | Meaning |
|---|---|---|---|
| `SESSION_HOURS` | `[1, 7, 12, 13, 14]` | fixed (OOS-selected; do not re-tune per session) | UTC session-open hours |
| `or_min` | `30` | `20–40` | opening-range length (minutes) |
| `tp_mult` | `1.5` | `1.0–2.5` | take-profit as multiple of OR width |
| `hold_bars` | `36` | (3h; not a sensitive knob) | max bars from OR/entry (M5) |
| `n_long` | `240` | `180–240` (360/480 also tested) | EMA length for bias (M5 bars) |
| `slope_lk` | `48` | — | bias slope lookback (M5 bars) |

**This is a parameter plateau, not a peak** — the numbers move smoothly across the ranges above, which
is robustness evidence (not a curve-fit spike). Keep the defaults; do not re-optimize on new data.

---

## 5. Reference implementation (build-ready)

Drop-in module mirroring the `bot/challenge_xau.py` shape: pure `signal()` + `position_size()` +
`backtest()` so it is unit-testable offline and the live layer is a thin wrapper. This is the canonical
spec — the Kronos port (§8) must reproduce these outputs bar-for-bar.

```python
"""bot/session_breakout.py — Bias-filtered opening-range breakout, XAUUSD M5.
Deployable port of strat_orb_biased (s5_intraday_research2.py). TAKER-compatible.
Design (zero discretion), evaluated on the last CLOSED M5 bar:
  bias  : EMA(240) level + 48-bar slope -> +1 up / -1 down / 0 flat
  window: session-open hours [1,7,12,13,14] UTC, first 30 min = opening range
  entry : STOP at OR boundary, only if break aligns with bias (long@hi / short@lo)
  stop  : opposite OR side (static)         tp: entry +/- tp_mult * OR width (static)
  exit  : stop / tp / 3h time-stop / flat by end of day
"""
from __future__ import annotations

USD_PER_POINT_PER_0_1_LOT = 10.0
SESSION_HOURS = (1, 7, 12, 13, 14)


def ema(vals, n):
    k = 2 / (n + 1); out = [vals[0]]
    for v in vals[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def bias_long_short(close, n_long=240, slope_lk=48):
    e = ema(close, n_long); b = [0] * len(close)
    for i in range(len(close)):
        if i < n_long + slope_lk:
            continue
        up = close[i] > e[i] and e[i] > e[i - slope_lk]
        dn = close[i] < e[i] and e[i] < e[i - slope_lk]
        b[i] = 1 if up else (-1 if dn else 0)
    return b


def position_size(equity, or_width_points, *, risk_pct=0.008, risk_floor=40.0,
                  min_lot=0.01, max_lot=0.50, lot_step=0.01):
    """Lots so a full-OR-width stop risks ~max(risk_floor, risk_pct*equity).
    NB: stop distance here is the OPENING-RANGE WIDTH, not k*ATR (unlike challenge_xau)."""
    risk_dollars = max(risk_floor, risk_pct * equity)
    if or_width_points <= 0:
        return 0.0, 0.0
    raw = risk_dollars / (or_width_points * (USD_PER_POINT_PER_0_1_LOT / 0.1))
    lot = max(min_lot, min(max_lot, round(raw / lot_step) * lot_step))
    actual = or_width_points * (USD_PER_POINT_PER_0_1_LOT / 0.1) * lot
    return round(lot, 2), round(actual, 2)


def build_signal(bars, *, session_hours=SESSION_HOURS, or_min=30, tp_mult=1.5,
                 hold_bars=36, n_long=240, slope_lk=48):
    """bars: list of (t,o,h,l,c), newest last, all CLOSED. Returns a pending order dict
    for the CURRENT session if a bias-aligned break is live on the last bar, else None:
      {side, entry, stop, tp, or_width}
    Live wrapper decides fill (buy-stop/sell-stop at `entry`, or market-on-break)."""
    t = [b[0] for b in bars]; h = [b[2] for b in bars]
    lo = [b[3] for b in bars]; c = [b[4] for b in bars]
    n = len(c)
    if n < n_long + slope_lk + 2:
        return None
    bias = bias_long_short(c, n_long, slope_lk)
    i = n - 1                      # last closed bar
    sh = t[i].hour
    if sh not in session_hours:
        return None
    day = t[i].date()
    # opening range for THIS session-hour, today
    or_idx = [k for k in range(n) if t[k].date() == day and t[k].hour == sh and t[k].minute < or_min]
    if len(or_idx) < 2 or t[i].minute < or_min:   # OR must be complete
        return None
    rng_hi = max(h[k] for k in or_idx); rng_lo = min(lo[k] for k in or_idx)
    rng = rng_hi - rng_lo
    if rng <= 0:
        return None
    # only within the hold window after OR close
    if i - or_idx[-1] > hold_bars:
        return None
    if h[i] >= rng_hi and bias[i] == 1:
        return {"side": "long", "entry": rng_hi, "stop": rng_lo,
                "tp": rng_hi + tp_mult * rng, "or_width": rng}
    if lo[i] <= rng_lo and bias[i] == -1:
        return {"side": "short", "entry": rng_lo, "stop": rng_hi,
                "tp": rng_lo - tp_mult * rng, "or_width": rng}
    return None
```

A `backtest()` (with cost model) should reproduce `strat_orb_biased` + `s5_intraday_research5.py`
outputs exactly; use `s5_intraday_research5.py` as the reference oracle for the acceptance test (§10).

---

## 6. Backtest & robustness evidence

Data: `reports/xau_m5_3y.csv`, 246k M5 bars, 2023-01 → 2026-06. Cost = taker round-trip, baseline
0.35pt (0.30 spread + $0.50 comm @0.1 lot), stressed to 0.45 / 0.60 / 0.80pt. All $ at **0.10 lot**.

| Check | Result |
|---|---|
| Frequency | **~4.1 trades/day** |
| Profit factor | **1.42 → 1.48** |
| Per-year | **positive every year** (2023, 2024, 2025, 2026-YTD) |
| Green weeks (in-sample) | 70% |
| Green weeks (OOS, test half 2025–26) | **71%** — did not degrade out-of-sample (not curve-fit) |
| Fresh-data check | last 4 full weeks (through 2026-06-29) all green |
| Cost stress @0.80pt (>2× real) | still **+1.09 pt/trade expectancy, 60% green weeks** |
| Parameter plateau | `or_min 20–40`, `tp_mult 1.0–2.5`, `n_long 180–240` all hold ~66–70% green, PF ~1.3–1.46 |

**Weekly P&L distribution** (@0.1 lot, cost 0.45pt):

| Metric | Value |
|---|---|
| Green weeks | 69% |
| Median green week | **+$329** |
| Median red week | **−$147** |
| Worst week | **−$979** |
| Longest red-week streak | **6 weeks** |
| Mean / stdev weekly | **+$290 / $612** |

Regenerate any of these with: `.venv/Scripts/python.exe s5_intraday_research5.py`.

---

## 7. Risk & position sizing

The backtest headline uses a flat 0.10 lot. **Do not deploy at 0.10 lot on a $5k challenge** — the
worst week (−$979) is ≈ −20% of a $5k account, which breaches the 10% overall drawdown limit.

- **Risk-size per trade off the OR width**, not a fixed lot (see `position_size()` in §5). The stop
  distance is the opening-range width, so lot = `risk_budget / (OR_width_pts × $10/0.1lot)`.
- A budget of **~0.5–0.8% equity per trade** (≈ $25–40 on $5k) keeps a full-stop loss small and pulls
  the worst week to roughly **−$200** (≈ −4%), inside the challenge limits.
- Fixed-lot fallback (if the engine can't range-size — see §8.5): **~0.02 lot**, the memory-validated
  size that bounds the worst week to ≈ −$200 on $5k.
- Keep `max_concurrent_positions = 1` and a **daily kill-switch** (e.g. stop after 2 consecutive losers
  or −$150 realized), consistent with the challenge doctrine.

---

## 8. Kronos ("chrono") engine integration spec

Mirror the `CHALLENGE_XAU` port (`kronos_challenge_xau.py` + the `entry_manager`/`position_monitor`
changes recorded in memory `kronos-challenge-xau-port`). The **key structural differences** from
`CHALLENGE_XAU` are called out in §8.2 — those are the exact modifications the builder must make.

### 8.1 New strategy file — `strategies/backtest_strategies/kronos_session_breakout.py`

```python
NAME = "SESSION_BREAKOUT"
CONFIG = StrategyConfig(
    name=NAME,
    description="M5 opening-range breakout, EMA240 bias, sessions [1,7,12,13,14] UTC, "
                "static OR-width stop + 1.5x-OR target. Port of strat_orb_biased.",
    cooldown_s=1800,               # >= OR length; combined with per-session state (see 8.3)
    session_start_hour=None,       # FIVE discrete hours -> gate inside get_signal, not via config
    session_end_hour=None,
    max_concurrent_positions=1,
)

SESSION_HOURS = (1, 7, 12, 13, 14)
_OR_MIN, _TP_MULT, _HOLD_BARS, _N_LONG, _SLOPE_LK = 30, 1.5, 36, 240, 48

def get_signal(w1m, w5m, w15m, now_utc) -> Signal | None:
    # 1. Use CLOSED M5 bars from w5m (drop the still-forming last bucket, like _resample_h4 does).
    # 2. now_utc.hour must be in SESSION_HOURS and now_utc.minute >= _OR_MIN (OR complete).
    # 3. Compute bias = EMA(240)+48-bar slope on M5 closes (backtest_strategies._kronos_indicators.ema).
    # 4. Build today's OR for this session-hour; require >= 2 bars and rng > 0.
    # 5. If last-closed-bar high >= rng_hi and bias == +1  -> BUY  (entry=rng_hi, sl=rng_lo, tp=rng_hi+1.5*rng)
    #    If last-closed-bar low  <= rng_lo and bias == -1  -> SELL (entry=rng_lo, sl=rng_hi, tp=rng_lo-1.5*rng)
    #    else None. trailing=False (STATIC sl/tp). reason="SESSION_BREAKOUT_LONG/SHORT".
    ...
```

### 8.2 How it differs from `CHALLENGE_XAU` (the exact modifications)

| Aspect | CHALLENGE_XAU (existing) | SESSION_BREAKOUT (this) |
|---|---|---|
| Working TF | H4 (resampled from 15m) | **M5** — use `w5m` directly, drop last forming bar; no resample |
| Bias | EMA20 > EMA50 | **EMA(240) level + 48-bar slope** on M5 |
| Entry trigger | close breaks Donchian(20) | **stop-entry at OR boundary** within a session window |
| Session gate | none (24/7) | **hours [1,7,12,13,14] UTC + first-30min OR**, gated inside `get_signal` |
| Stop | 3×ATR chandelier **trailing** (`trailing=True`) | **static** = opposite OR side (`trailing=False`) |
| Take-profit | far placeholder (backstop only) | **real static TP** = entry ± 1.5×OR width |
| Exit engine | `position_monitor._ratchet_trail()` | **static broker SL/TP** + a **3h max-hold** time-close |
| Cooldown | 14400s (1 H4 bar) | 1800s + per-session single-entry state |

The good news: because the stop and TP are **static**, this port is **simpler** than CHALLENGE_XAU —
it does **not** need the trailing-ratchet path. Set `Signal(trailing=False)` and supply real
`stop_loss` and `take_profit`; the existing pre-trailing `entry_manager` path (static broker SL/TP)
handles it. The one new requirement is the **max-hold time-close** (§8.3).

### 8.3 Execution nuances (read before coding)

1. **Entry fill — the single most important decision.** The backtest fills at the OR boundary
   (`rng_hi`/`rng_lo`) via an implicit stop order. Two options:
   - **Preferred:** place a **buy-stop / sell-stop pending order** at the OR boundary, valid only during
     the session/hold window, cancel on expiry. This matches the backtest fills.
   - **Fallback (if the engine is market-only):** enter **market at the break-bar close**. This adds
     ~half-spread to a few ticks of slippage vs the backtest. The **0.80pt cost stress (still +1.09pt,
     60% green) already covers this** — but MEASURE live XAU spread in the session hours before risking
     the account and confirm it stays ≤ ~0.45pt.
2. **Max-hold time stop.** Close the position at market after **36 closed M5 bars (10800s = 3h)** from
   entry if neither SL nor TP has hit, and **flatten by end of the UTC day**. If Kronos already has a
   per-leg max-hold (the combined suite references one), reuse it with `max_hold_s = 10800`; otherwise
   add a small timer in `position_monitor`.
3. **One entry per session window.** After an entry for session-hour `sh` on a given day, suppress
   further `SESSION_BREAKOUT` entries until the next session hour. `cooldown_s` alone is a coarse proxy
   (the NY hours 12/13/14 are adjacent) — prefer explicit per-`(date, session_hour)` state.
4. **Causality.** Only evaluate CLOSED M5 bars; never the forming bar. The OR must be *complete*
   (`now_utc.minute >= or_min`) before any entry is allowed that session.

### 8.4 Registrations & files to touch (mirrors the CHALLENGE_XAU port)

- `strategies/backtest_strategies/kronos_session_breakout.py` — NEW (§8.1).
- `strategies/strategy/entry_manager.py` — register `"SESSION_BREAKOUT" -> "Session Breakout M5 ORB"`; route it down the **static SL/TP** path (not the trailing path).
- `position_manager/position_monitor.py` — add the **3h max-hold** time-close if not already available.
- `tests/test_session_breakout.py` — new (§10).
- `compose.yml` + `strategies/db/deploy_session_breakout.py` — mirror `deploy_challenge_xau.py`. The M5 runner window must hold ≥ `n_long + slope_lk + 2` = **~290 closed M5 bars** (≈ 1 day) — size `RESEARCH_WIN_5M` / `RESEARCH_DAYS_5M` for ~2 days of M5 headroom.

### 8.5 Sizing caveat (unchanged from CHALLENGE_XAU)

Kronos uses a **fixed lot per strategy** — the OR-width `position_size()` in §5 is **not** applied by
the engine. Two paths: (a) run a small fixed lot (**~0.02**, `SESSION_BREAKOUT_LOT` env) and accept the
worst-week ≈ −$200 profile; or (b) extend the engine to pass OR width into a range-based sizer for
proper per-trade risk. Path (a) is the faster route to a compliant challenge run.

### "Modify the execute challenge strategy to match"

If the intent is to make the **challenge account** trade this instead of the H4 trend-follow: bind the
challenge `UserBroker`/`UserStrategy` to `SESSION_BREAKOUT` in `deploy_*`, set the fixed lot to ~0.02,
and keep the daily kill-switch. Functionally this swaps the low-frequency H4 Donchian edge (~6/mo,
PF 1.83, trailing) for the higher-frequency M5 ORB edge (~4/day, PF ~1.45, static SL/TP). Keep
`CHALLENGE_XAU` deployed in parallel unless you deliberately want to retire it — they are uncorrelated
(different TF, different trigger) and could run as two variations on separate sub-accounts.

---

## 9. Honest caveats & failure modes

- **"Profitable every week" is not literally achievable.** Expect red weeks and multi-week red clusters
  (6 in a row historically). The claim is ~70% green weeks over a long horizon, not a weekly money-maker.
- **Trend-regime dependent.** The edge is trend-aligned; in a prolonged choppy/flat regime the bias sits
  at 0 (few trades) or whipsaws. A losing quarter is possible.
- **Fill assumption.** The backtest's OR-boundary fill is mildly optimistic vs market-on-break; the cost
  stress covers it, but a persistently wide live spread (> ~0.45pt) erodes the edge — verify before size.
- **Not maker-dependent (this is a strength).** Unlike `snap_ict_maker`, this pays the spread and still
  profits, so it is deployable on the taker-only FundingPips account.
- **Fixed-lot risk.** At 0.10 lot it over-risks a $5k account; you MUST run ~0.02 lot or range-size.

---

## 10. Acceptance tests (how to validate the port)

1. **Oracle parity:** feed `reports/xau_m5_3y.csv` through the ported `get_signal`/backtest and confirm
   it reproduces `s5_intraday_research5.py`: ~4.1 trades/day, PF ~1.42–1.48, positive every year,
   ~69–71% green weeks, worst week ≈ −$979 @0.1 lot / cost 0.45pt.
2. **Causality test:** assert no signal uses the forming bar and none fires before the OR is complete
   (`minute >= or_min`).
3. **Session gate test:** assert entries occur only in hours {1,7,12,13,14} and at most one per
   `(date, session_hour)`.
4. **Bias gate test:** assert no long when bias ≤ 0 and no short when bias ≥ 0.
5. **Static-exit test:** assert `trailing=False`, and that SL/TP equal the OR opposite side and
   entry ± 1.5×OR respectively.
6. **Sizing test:** assert a full-stop loss at the chosen lot stays within the daily loss limit.

Run under the repo `.venv` (`.venv/Scripts/python.exe`), matching the CHALLENGE_XAU test convention
(`tests/test_challenge_xau.py`).
